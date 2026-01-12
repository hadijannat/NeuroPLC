use crate::auth::{AuthConfig, TokenValidator};
use crate::metrics::{
    AGENT_CONFIDENCE, AGENT_TARGET_RPM, AUTH_FAILURES, AUTH_MISSING, BRIDGE_CONNECTED,
    RECOMMENDATION_EXPIRED, RECOMMENDATION_OUT_OF_ORDER,
};
use crate::protocol::{HelloMsg, IncomingMessage, StateMsg};
#[cfg(feature = "proto")]
use crate::protocol_proto::proto;
use crate::tls::{build_server_config, TlsConfig};
use core_spine::{AgentRecommendation, StateExchange, TimeBase};
#[cfg(feature = "proto")]
use prost::Message;
use rustls::{ServerConnection, StreamOwned};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{atomic::AtomicBool, Arc};
use std::time::{Duration, Instant};
use tracing::{debug, error, info, instrument, trace, warn, Span};

pub struct BridgeConfig {
    pub bind_addr: String,
    pub publish_interval: Duration,
    pub tls: TlsConfig,
    pub auth: AuthConfig,
    pub require_handshake: bool,
    pub wire_protocol: WireProtocol,
}

impl Default for BridgeConfig {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:7000".to_string(),
            publish_interval: Duration::from_millis(100),
            tls: TlsConfig::default(),
            auth: AuthConfig::default(),
            require_handshake: false,
            wire_protocol: WireProtocol::JsonLines,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WireProtocol {
    JsonLines,
    Protobuf,
}

impl WireProtocol {
    pub fn as_str(&self) -> &'static str {
        match self {
            WireProtocol::JsonLines => "json",
            WireProtocol::Protobuf => "proto",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value.to_ascii_lowercase().as_str() {
            "json" | "jsonl" | "jsonlines" => Some(WireProtocol::JsonLines),
            "proto" | "protobuf" => Some(WireProtocol::Protobuf),
            _ => None,
        }
    }
}

enum BridgeStream {
    Plain(TcpStream),
    Tls(Box<StreamOwned<ServerConnection, TcpStream>>),
}

impl Read for BridgeStream {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        match self {
            BridgeStream::Plain(s) => s.read(buf),
            BridgeStream::Tls(s) => s.read(buf),
        }
    }
}

impl Write for BridgeStream {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        match self {
            BridgeStream::Plain(s) => s.write(buf),
            BridgeStream::Tls(s) => s.write(buf),
        }
    }

    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            BridgeStream::Plain(s) => s.flush(),
            BridgeStream::Tls(s) => s.flush(),
        }
    }
}

#[derive(Debug)]
struct InboundState {
    last_sequence: Option<u64>,
    handshake_seen: bool,
    capabilities: Vec<String>,
    client_id: Option<String>,
}

impl InboundState {
    fn new() -> Self {
        Self {
            last_sequence: None,
            handshake_seen: false,
            capabilities: Vec::new(),
            client_id: None,
        }
    }

    fn reset(&mut self) {
        self.last_sequence = None;
        self.handshake_seen = false;
        self.capabilities.clear();
        self.client_id = None;
    }

    fn accept_sequence(&mut self, sequence: u64) -> bool {
        if sequence == 0 {
            warn!("Recommendation sequence missing or zero");
            return false;
        }
        if let Some(last) = self.last_sequence {
            if sequence <= last {
                warn!(
                    sequence,
                    last_sequence = last,
                    "Out-of-order recommendation sequence"
                );
                return false;
            }
        }
        self.last_sequence = Some(sequence);
        true
    }

    fn note_handshake(&mut self, hello: &HelloMsg) {
        self.handshake_seen = true;
        self.capabilities = hello.capabilities.clone();
        self.client_id = hello.client_id.clone();
    }
}

pub fn run_bridge(
    exchange: Arc<StateExchange>,
    timebase: TimeBase,
    config: BridgeConfig,
    stop: Arc<AtomicBool>,
) {
    let listener = TcpListener::bind(&config.bind_addr)
        .unwrap_or_else(|e| panic!("Failed to bind {}: {}", config.bind_addr, e));
    listener
        .set_nonblocking(true)
        .expect("Failed to set nonblocking");

    info!(
        addr = %config.bind_addr,
        tls = config.tls.enabled,
        auth = config.auth.enabled,
        protocol = %config.wire_protocol.as_str(),
        "Bridge listening"
    );

    let tls_config = if config.tls.enabled {
        match build_server_config(&config.tls) {
            Ok(c) => Some(c),
            Err(e) => {
                error!(error = %e, "Failed to configure TLS");
                return;
            }
        }
    } else {
        None
    };

    let validator = if config.auth.enabled {
        Some(TokenValidator::from_config(&config.auth))
    } else {
        None
    };

    let mut client: Option<BridgeStream> = None;
    let mut recv_buf: Vec<u8> = Vec::with_capacity(4096);
    let mut send_buf: Vec<u8> = Vec::new();
    let mut send_offset: usize = 0;
    let mut last_publish = Instant::now();
    let mut state_sequence: u64 = 0;
    let mut inbound_state = InboundState::new();

    loop {
        if stop.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        if client.is_none() {
            match listener.accept() {
                Ok((stream, addr)) => {
                    info!(client_addr = %addr, "Bridge client connected");
                    stream
                        .set_nonblocking(true)
                        .expect("Failed to set nonblocking on client");

                    if let Some(tls_cfg) = &tls_config {
                        match ServerConnection::new(tls_cfg.clone()) {
                            Ok(conn) => {
                                client = Some(BridgeStream::Tls(Box::new(StreamOwned::new(
                                    conn, stream,
                                ))));
                            }
                            Err(e) => {
                                error!("Failed to create TLS connection state: {}", e);
                            }
                        }
                    } else {
                        client = Some(BridgeStream::Plain(stream));
                    }
                    BRIDGE_CONNECTED.set(1.0);
                }
                Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(err) => {
                    warn!("Bridge accept error: {}", err);
                }
            }
        }

        let mut drop_client = false;
        if let Some(stream) = client.as_mut() {
            // Receive data
            let mut temp = [0u8; 1024];
            match stream.read(&mut temp) {
                Ok(0) => {
                    info!("Bridge client disconnected");
                    drop_client = true;
                    BRIDGE_CONNECTED.set(0.0);
                }
                Ok(n) => {
                    recv_buf.extend_from_slice(&temp[..n]);
                    match config.wire_protocol {
                        WireProtocol::JsonLines => {
                            while let Some(pos) = recv_buf.iter().position(|b| *b == b'\n') {
                                let line = recv_buf.drain(..=pos).collect::<Vec<u8>>();
                                if let Ok(text) = std::str::from_utf8(&line) {
                                    let trimmed = text.trim();
                                    if trimmed.is_empty() {
                                        continue;
                                    }
                                    if let Some(msg) = IncomingMessage::parse(trimmed) {
                                        handle_incoming(
                                            msg,
                                            &exchange,
                                            &timebase,
                                            &validator,
                                            config.require_handshake,
                                            &mut inbound_state,
                                        );
                                    }
                                }
                            }
                        }
                        WireProtocol::Protobuf => {
                            #[cfg(feature = "proto")]
                            {
                                const MAX_FRAME_BYTES: usize = 256 * 1024;
                                loop {
                                    if recv_buf.len() < 4 {
                                        break;
                                    }
                                    let len = u32::from_be_bytes([
                                        recv_buf[0],
                                        recv_buf[1],
                                        recv_buf[2],
                                        recv_buf[3],
                                    ]) as usize;
                                    if len > MAX_FRAME_BYTES {
                                        warn!(len, "Dropping client with oversized frame");
                                        drop_client = true;
                                        break;
                                    }
                                    if recv_buf.len() < 4 + len {
                                        break;
                                    }
                                    let payload = recv_buf[4..4 + len].to_vec();
                                    recv_buf.drain(..4 + len);
                                    match proto::WireMessage::decode(payload.as_slice())
                                        .ok()
                                        .and_then(|msg| IncomingMessage::try_from(msg).ok())
                                    {
                                        Some(msg) => {
                                            handle_incoming(
                                                msg,
                                                &exchange,
                                                &timebase,
                                                &validator,
                                                config.require_handshake,
                                                &mut inbound_state,
                                            );
                                        }
                                        None => {
                                            warn!("Failed to decode protobuf message");
                                        }
                                    }
                                }
                            }
                            #[cfg(not(feature = "proto"))]
                            {
                                warn!("Protobuf wire protocol requested but 'proto' feature is disabled");
                                drop_client = true;
                            }
                        }
                    }
                }
                Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(err) => {
                    warn!(error = %err, "Bridge read error");
                    drop_client = true;
                    BRIDGE_CONNECTED.set(0.0);
                }
            }

            // Publish state
            if send_buf.is_empty() && last_publish.elapsed() >= config.publish_interval {
                state_sequence = state_sequence.wrapping_add(1);
                let snapshot = exchange.read_state();
                match config.wire_protocol {
                    WireProtocol::JsonLines => {
                        let msg = StateMsg {
                            msg_type: "state",
                            protocol_version: crate::protocol::ProtocolVersion::v1(),
                            sequence: state_sequence,
                            timestamp_us: snapshot.timestamp_us,
                            cycle_count: snapshot.cycle_count,
                            safety_state: snapshot.safety_state.as_str(),
                            unix_us: timebase.unix_us(),
                            motor_speed_rpm: snapshot.motor_speed_rpm,
                            motor_temp_c: snapshot.motor_temp_c,
                            pressure_bar: snapshot.pressure_bar,
                            cycle_jitter_us: snapshot.cycle_jitter_us,
                        };
                        if let Ok(line) = serde_json::to_string(&msg) {
                            send_buf = line.into_bytes();
                            send_buf.push(b'\n');
                            send_offset = 0;
                            trace!(sequence = state_sequence, "Bridge queued state frame");
                        }
                    }
                    WireProtocol::Protobuf => {
                        #[cfg(feature = "proto")]
                        {
                            let msg = proto::State {
                                protocol_version: Some(proto::ProtocolVersion {
                                    major: 1,
                                    minor: 0,
                                }),
                                sequence: state_sequence,
                                timestamp_us: snapshot.timestamp_us,
                                cycle_count: snapshot.cycle_count,
                                safety_state: snapshot.safety_state.as_str().to_string(),
                                unix_us: timebase.unix_us(),
                                motor_speed_rpm: snapshot.motor_speed_rpm,
                                motor_temp_c: snapshot.motor_temp_c,
                                pressure_bar: snapshot.pressure_bar,
                                cycle_jitter_us: snapshot.cycle_jitter_us,
                            };
                            let wire = proto::WireMessage {
                                payload: Some(proto::wire_message::Payload::State(msg)),
                            };
                            let mut frame = Vec::new();
                            if wire.encode(&mut frame).is_ok() {
                                let len = frame.len() as u32;
                                send_buf = len.to_be_bytes().to_vec();
                                send_buf.extend_from_slice(&frame);
                                send_offset = 0;
                            }
                        }
                        #[cfg(not(feature = "proto"))]
                        {
                            warn!(
                                "Protobuf wire protocol requested but 'proto' feature is disabled"
                            );
                        }
                    }
                }
                last_publish = Instant::now();
            }

            if !send_buf.is_empty() {
                match stream.write(&send_buf[send_offset..]) {
                    Ok(0) => {
                        info!("Bridge client disconnected");
                        drop_client = true;
                        BRIDGE_CONNECTED.set(0.0);
                    }
                    Ok(n) => {
                        send_offset += n;
                        trace!(bytes = n, offset = send_offset, "Bridge wrote state bytes");
                        if send_offset >= send_buf.len() {
                            send_buf.clear();
                            send_offset = 0;
                        }
                    }
                    Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {}
                    Err(err) => {
                        warn!(error = %err, "Bridge write error");
                        drop_client = true;
                        BRIDGE_CONNECTED.set(0.0);
                    }
                }
            }
        }

        if drop_client {
            client = None;
            recv_buf.clear();
            send_buf.clear();
            send_offset = 0;
            inbound_state.reset();
        }

        std::thread::sleep(Duration::from_millis(5));
    }
}

#[instrument(skip(exchange, timebase, validator), fields(reasoning_hash))]
fn handle_incoming(
    msg: IncomingMessage,
    exchange: &StateExchange,
    timebase: &TimeBase,
    validator: &Option<TokenValidator>,
    require_handshake: bool,
    inbound_state: &mut InboundState,
) {
    match msg {
        IncomingMessage::Hello(hello) => {
            if !hello.protocol_version.is_supported() {
                warn!(
                    major = hello.protocol_version.major,
                    minor = hello.protocol_version.minor,
                    "Unsupported protocol version"
                );
                return;
            }
            inbound_state.note_handshake(&hello);
            info!(
                client_id = ?hello.client_id,
                capabilities = ?hello.capabilities,
                "Bridge handshake received"
            );
        }
        IncomingMessage::Recommendation(rec) => {
            Span::current().record("reasoning_hash", rec.reasoning_hash.as_str());

            if !rec.protocol_version.is_supported() {
                warn!(
                    major = rec.protocol_version.major,
                    minor = rec.protocol_version.minor,
                    "Unsupported protocol version"
                );
                return;
            }

            if require_handshake && !inbound_state.handshake_seen {
                warn!("Recommendation received before handshake");
                return;
            }

            if !inbound_state.accept_sequence(rec.sequence) {
                RECOMMENDATION_OUT_OF_ORDER.inc();
                return;
            }

            if rec.ttl_ms == 0 {
                warn!("Missing recommendation TTL");
                return;
            }
            if rec.issued_at_unix_us == 0 {
                warn!("Missing recommendation issued_at_unix_us");
                return;
            }
            let now_unix_us = timebase.unix_us();
            const MAX_CLOCK_SKEW_MS: u64 = 5_000;
            let max_skew_us = MAX_CLOCK_SKEW_MS * 1_000;
            if rec.issued_at_unix_us > now_unix_us.saturating_add(max_skew_us) {
                warn!(
                    issued_at_unix_us = rec.issued_at_unix_us,
                    now_unix_us, "Recommendation timestamp is too far in the future"
                );
                return;
            }
            let age_ms = now_unix_us
                .saturating_sub(rec.issued_at_unix_us)
                .saturating_div(1_000);
            if age_ms > rec.ttl_ms {
                warn!(age_ms, ttl_ms = rec.ttl_ms, "Recommendation expired");
                RECOMMENDATION_EXPIRED.inc();
                return;
            }

            // Check authentication
            if let Some(val) = validator {
                match &rec.auth_token {
                    Some(token) => {
                        if let Err(e) = val.validate(token) {
                            warn!(error = %e, "Invalid auth token");
                            AUTH_FAILURES.inc();
                            return;
                        }
                    }
                    None => {
                        warn!("Missing auth token");
                        AUTH_MISSING.inc();
                        return;
                    }
                }
            }

            let hash = match hex_to_32(&rec.reasoning_hash) {
                Some(h) => h,
                None => {
                    warn!(hash = %rec.reasoning_hash, "Invalid reasoning_hash hex length");
                    return;
                }
            };

            let target = rec.target_speed_rpm;
            if let Some(val) = target {
                if !val.is_finite() {
                    warn!(value = %val, "Ignoring non-finite recommendation");
                    return;
                }
            }
            if !(0.0..=1.0).contains(&rec.confidence) {
                warn!(
                    confidence = rec.confidence,
                    "Ignoring recommendation with invalid confidence"
                );
                return;
            }

            // Update metrics
            if let Some(target_val) = target {
                AGENT_TARGET_RPM.set(target_val);
            }
            AGENT_CONFIDENCE.set(rec.confidence as f64);

            debug!(
                target_speed = ?target,
                confidence = rec.confidence,
                "Recommendation received"
            );

            let stamped = AgentRecommendation {
                timestamp_us: timebase.now_us(),
                target_speed_rpm: target,
                confidence: rec.confidence,
                reasoning_hash: hash,
            };

            exchange.submit_recommendation(stamped);
        }
    }
}

fn hex_to_32(input: &str) -> Option<[u8; 32]> {
    if input.len() != 64 {
        return None;
    }
    let mut out = [0u8; 32];
    let bytes = input.as_bytes();
    let mut i = 0;
    while i < 32 {
        let hi = from_hex_digit(bytes[i * 2])?;
        let lo = from_hex_digit(bytes[i * 2 + 1])?;
        out[i] = (hi << 4) | lo;
        i += 1;
    }
    Some(out)
}

fn from_hex_digit(b: u8) -> Option<u8> {
    match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        b'A'..=b'F' => Some(b - b'A' + 10),
        _ => None,
    }
}
