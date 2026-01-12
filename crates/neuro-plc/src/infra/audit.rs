//! Audit logging for safety-critical events.
//!
//! This module provides persistent logging of all safety-relevant events
//! including recommendations, rejections, and system state changes.

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Write};
use std::path::Path;
use std::sync::Mutex;

/// Types of events that are logged in the audit trail
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditEventType {
    /// AI recommendation received from agent
    RecommendationReceived,
    /// Recommendation was applied to actuator
    RecommendationApplied,
    /// Recommendation was rejected by safety firewall
    SafetyRejection,
    /// Agent client connected to bridge
    ClientConnected,
    /// Agent client disconnected
    ClientDisconnected,
    /// Emergency stop triggered
    EmergencyStop,
    /// Configuration change applied
    ConfigChange,
    /// System startup
    SystemStart,
    /// System shutdown
    SystemShutdown,
    /// Watchdog timeout occurred
    WatchdogTimeout,
}

/// A single audit log entry
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    /// Monotonic timestamp in microseconds
    pub timestamp_us: u64,
    /// Wall-clock Unix timestamp in microseconds
    pub unix_us: u64,
    /// Type of event being logged
    pub event_type: AuditEventType,
    /// Additional event-specific details
    pub details: serde_json::Value,
}

/// A record with hash chaining for tamper evidence
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditRecord {
    pub entry: AuditEntry,
    pub prev_hash: String,
    pub entry_hash: String,
}

struct AuditState {
    writer: BufWriter<File>,
    last_hash: String,
}

/// Thread-safe audit logger that writes to a JSONL file
pub struct AuditLogger {
    state: Mutex<AuditState>,
}

impl AuditLogger {
    /// Create a new audit logger writing to the specified path.
    /// The file is opened in append mode to preserve existing logs.
    pub fn new(path: &Path) -> std::io::Result<Self> {
        // Create parent directories if they don't exist
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let file = OpenOptions::new().create(true).append(true).open(path)?;

        Ok(Self {
            state: Mutex::new(AuditState {
                writer: BufWriter::with_capacity(8192, file),
                last_hash: String::from("0"),
            }),
        })
    }

    /// Log an audit entry. This is thread-safe and can be called from any thread.
    pub fn log(&self, entry: AuditEntry) -> std::io::Result<()> {
        let mut state = self.state.lock().unwrap();
        let prev_hash = state.last_hash.clone();
        let entry_hash = hash_entry(&entry, &prev_hash);
        let record = AuditRecord {
            entry,
            prev_hash,
            entry_hash: entry_hash.clone(),
        };

        serde_json::to_writer(&mut state.writer, &record)?;
        state.writer.write_all(b"\n")?;
        state.writer.flush()?;
        state.last_hash = entry_hash;
        Ok(())
    }

    /// Convenience method to log with just event type and details
    pub fn log_event(
        &self,
        timestamp_us: u64,
        unix_us: u64,
        event_type: AuditEventType,
        details: serde_json::Value,
    ) -> std::io::Result<()> {
        self.log(AuditEntry {
            timestamp_us,
            unix_us,
            event_type,
            details,
        })
    }
}

pub fn hash_entry(entry: &AuditEntry, prev_hash: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(prev_hash.as_bytes());
    let entry_bytes = serde_json::to_vec(entry).unwrap_or_default();
    hasher.update(&entry_bytes);
    to_hex(&hasher.finalize())
}

pub fn hash_bytes(bytes: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(bytes);
    to_hex(&hasher.finalize())
}

pub fn hash_str(value: &str) -> String {
    hash_bytes(value.as_bytes())
}

fn to_hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write;
        let _ = write!(&mut out, "{:02x}", byte);
    }
    out
}

/// Details for a safety rejection event
#[allow(dead_code)]
#[derive(Debug, Clone, Serialize)]
pub struct SafetyRejectionDetails {
    pub requested_speed: f64,
    pub current_speed: f64,
    pub current_temp: f64,
    pub violation_type: String,
    pub limit_value: f64,
    pub reasoning_hash: String,
}

/// Details for a recommendation received event
#[allow(dead_code)]
#[derive(Debug, Clone, Serialize)]
pub struct RecommendationReceivedDetails {
    pub target_speed: Option<f64>,
    pub confidence: f32,
    pub reasoning_hash: String,
    pub client_addr: Option<String>,
}

/// Details for a recommendation applied event
#[allow(dead_code)]
#[derive(Debug, Clone, Serialize)]
pub struct RecommendationAppliedDetails {
    pub target_speed: f64,
    pub confidence: f32,
    pub previous_speed: f64,
    pub reasoning_hash: String,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Read;
    use tempfile::tempdir;

    #[test]
    fn test_audit_logger_writes_jsonl() {
        let dir = tempdir().unwrap();
        let path = dir.path().join("audit.jsonl");

        let logger = AuditLogger::new(&path).unwrap();

        logger
            .log_event(
                1000,
                1704067200000000,
                AuditEventType::SystemStart,
                serde_json::json!({"version": "0.1.0"}),
            )
            .unwrap();

        logger
            .log_event(
                2000,
                1704067201000000,
                AuditEventType::RecommendationReceived,
                serde_json::json!({"target_speed": 500.0, "confidence": 0.9}),
            )
            .unwrap();

        // Read back and verify
        let mut file = File::open(&path).unwrap();
        let mut contents = String::new();
        file.read_to_string(&mut contents).unwrap();

        let lines: Vec<&str> = contents.lines().collect();
        assert_eq!(lines.len(), 2);

        let first: AuditRecord = serde_json::from_str(lines[0]).unwrap();
        let second: AuditRecord = serde_json::from_str(lines[1]).unwrap();

        assert_eq!(first.entry.timestamp_us, 1000);
        assert_eq!(second.entry.timestamp_us, 2000);
        assert_eq!(second.prev_hash, first.entry_hash);
    }
}
