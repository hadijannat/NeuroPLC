use crate::protocol::{IncomingMessage, StateMsg};
use core_spine::{AgentRecommendation, StateExchange, TimeBase};
use log::{info, warn};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{atomic::AtomicBool, Arc};
use std::time::{Duration, Instant};

pub struct BridgeConfig {
    pub bind_addr: String,
    pub publish_interval: Duration,
}

impl Default for BridgeConfig {
    fn default() -> Self {
        Self {
            bind_addr: "127.0.0.1:7000".to_string(),
            publish_interval: Duration::from_millis(100),
        }
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

    info!("Bridge listening on {}", config.bind_addr);

    let mut client: Option<TcpStream> = None;
    let mut recv_buf: Vec<u8> = Vec::with_capacity(4096);
    let mut last_publish = Instant::now();

    loop {
        if stop.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        if client.is_none() {
            match listener.accept() {
                Ok((stream, addr)) => {
                    info!("Bridge client connected from {}", addr);
                    stream
                        .set_nonblocking(true)
                        .expect("Failed to set nonblocking on client");
                    client = Some(stream);
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
                }
                Ok(n) => {
                    recv_buf.extend_from_slice(&temp[..n]);
                    while let Some(pos) = recv_buf.iter().position(|b| *b == b'\n') {
                        let line = recv_buf.drain(..=pos).collect::<Vec<u8>>();
                        if let Ok(text) = std::str::from_utf8(&line) {
                            let trimmed = text.trim();
                            if trimmed.is_empty() {
                                continue;
                            }
                            if let Some(msg) = IncomingMessage::parse(trimmed) {
                                handle_incoming(msg, &exchange, &timebase);
                            }
                        }
                    }
                }
                Err(err) if err.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(err) => {
                    warn!("Bridge read error: {}", err);
                    drop_client = true;
                }
            }

            // Publish state
            if last_publish.elapsed() >= config.publish_interval {
                let snapshot = exchange.read_state();
                let msg = StateMsg {
                    msg_type: "state",
                    timestamp_us: snapshot.timestamp_us,
                    unix_us: timebase.unix_us(),
                    motor_speed_rpm: snapshot.motor_speed_rpm,
                    motor_temp_c: snapshot.motor_temp_c,
                    pressure_bar: snapshot.pressure_bar,
                    cycle_jitter_us: snapshot.cycle_jitter_us,
                };
                if let Ok(line) = serde_json::to_string(&msg) {
                    if let Err(err) = stream.write_all(line.as_bytes()) {
                        warn!("Bridge write error: {}", err);
                        drop_client = true;
                    } else if let Err(err) = stream.write_all(b"\n") {
                        warn!("Bridge write error: {}", err);
                        drop_client = true;
                    }
                }
                last_publish = Instant::now();
            }
        }

        if drop_client {
            client = None;
            recv_buf.clear();
        }

        std::thread::sleep(Duration::from_millis(5));
    }
}

fn handle_incoming(msg: IncomingMessage, exchange: &StateExchange, timebase: &TimeBase) {
    match msg {
        IncomingMessage::Recommendation(rec) => {
            let _client_unix = rec.client_unix_us;
            let hash = match hex_to_32(&rec.reasoning_hash) {
                Some(h) => h,
                None => {
                    warn!("Invalid reasoning_hash hex length");
                    return;
                }
            };

            let target = rec.target_speed_rpm;
            if let Some(val) = target {
                if !val.is_finite() {
                    warn!("Ignoring non-finite recommendation");
                    return;
                }
            }
            if !(0.0..=1.0).contains(&rec.confidence) {
                warn!("Ignoring recommendation with invalid confidence");
                return;
            }

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
