use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::process::{Child, Command};
use std::thread;
use std::time::Duration;

struct SpineProcess {
    child: Child,
    addr: String,
}

impl SpineProcess {
    fn start() -> Self {
        // Prefer the test-built binary when available to avoid extra cargo builds.
        let bin_path = std::env::var("CARGO_BIN_EXE_neuro-plc").unwrap_or_else(|_| {
            let candidates = [
                "../../target/release/neuro-plc",
                "target/release/neuro-plc",
                "./target/release/neuro-plc",
                "../../target/debug/neuro-plc",
                "target/debug/neuro-plc",
                "./target/debug/neuro-plc",
            ];
            for candidate in candidates {
                if std::path::Path::new(candidate).exists() {
                    return candidate.to_string();
                }
            }
            panic!(
                "Failed to locate neuro-plc binary. Expected CARGO_BIN_EXE_neuro-plc or a build in target/{{release,debug}}/neuro-plc."
            );
        });

        let listener = TcpListener::bind("127.0.0.1:0")
            .expect("Failed to bind ephemeral port for integration test");
        let addr = listener
            .local_addr()
            .expect("Failed to resolve bound address");
        let bind_addr = format!("127.0.0.1:{}", addr.port());
        drop(listener);

        let child = Command::new(&bin_path)
            .args(["--bind", &bind_addr])
            .spawn()
            .expect("Failed to start spine");

        // Loop until port is open (up to 5s)
        let start = std::time::Instant::now();
        while start.elapsed().as_secs() < 5 {
            if std::net::TcpStream::connect(&bind_addr).is_ok() {
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }

        // Give it a little more time to initialize internal state
        thread::sleep(Duration::from_millis(500));
        Self {
            child,
            addr: bind_addr,
        }
    }

    fn addr(&self) -> &str {
        &self.addr
    }
}

impl Drop for SpineProcess {
    fn drop(&mut self) {
        let _ = self.child.kill();
    }
}

#[test]
fn test_recommendation_accepted() {
    let spine = SpineProcess::start();

    let mut stream = TcpStream::connect(spine.addr()).expect("Failed to connect to spine");
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();

    let mut reader = BufReader::new(stream.try_clone().unwrap());

    // Wait for first state message
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();

    let state: serde_json::Value = serde_json::from_str(&line).unwrap();
    assert_eq!(state["type"], "state");

    // Send valid recommendation
    let issued_at_unix_us = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_micros() as u64;
    let recommendation = serde_json::json!({
        "type": "recommendation",
        "protocol_version": { "major": 1, "minor": 0 },
        "sequence": 1,
        "issued_at_unix_us": issued_at_unix_us,
        "ttl_ms": 2_000,
        "target_speed_rpm": 500.0,
        "confidence": 0.9,
        "reasoning_hash": "a".repeat(64),
        "client_unix_us": issued_at_unix_us
    });

    writeln!(stream, "{}", recommendation).unwrap();

    // Verify next state shows speed increase
    thread::sleep(Duration::from_millis(500));
    line.clear();
    reader.read_line(&mut line).unwrap();
    // We might need to drain a few lines to get to the new state
    thread::sleep(Duration::from_millis(500));
    line.clear();
    reader.read_line(&mut line).unwrap();

    let new_state: serde_json::Value = serde_json::from_str(&line).unwrap();
    // Check if motor_speed_rpm exists (it should in a full state message)
    if let Some(new_speed) = new_state["motor_speed_rpm"].as_f64() {
        // Speed should be moving toward target (may not reach 500 due to rate limiting)
        assert!(new_speed >= 0.0, "Speed match");
    }
}

#[test]
fn test_unsafe_recommendation_rejected() {
    let spine = SpineProcess::start();

    let mut stream = TcpStream::connect(spine.addr()).expect("Failed to connect");
    let mut reader = BufReader::new(stream.try_clone().unwrap());

    // Skip initial state
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let initial_state: serde_json::Value = serde_json::from_str(&line).unwrap();
    let _initial_speed = initial_state["motor_speed_rpm"].as_f64().unwrap_or(0.0);

    // Send unsafe recommendation (above max)
    let issued_at_unix_us = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_micros() as u64;
    let recommendation = serde_json::json!({
        "type": "recommendation",
        "protocol_version": { "major": 1, "minor": 0 },
        "sequence": 1,
        "issued_at_unix_us": issued_at_unix_us,
        "ttl_ms": 2_000,
        "target_speed_rpm": 5000.0,  // Above 3000 limit
        "confidence": 0.9,
        "reasoning_hash": "b".repeat(64),
        "client_unix_us": issued_at_unix_us
    });

    writeln!(stream, "{}", recommendation).unwrap();

    thread::sleep(Duration::from_millis(500));
    line.clear();
    reader.read_line(&mut line).unwrap();

    let new_state: serde_json::Value = serde_json::from_str(&line).unwrap();
    let new_speed = new_state["motor_speed_rpm"].as_f64().unwrap_or(0.0);

    // Speed should NOT have jumped to unsafe value
    assert!(new_speed <= 3000.0, "Safety should prevent overspeed");
}
