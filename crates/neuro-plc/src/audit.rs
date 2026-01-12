//! Audit logging for safety-critical events.
//!
//! This module provides persistent logging of all safety-relevant events
//! including recommendations, rejections, and system state changes.

use serde::{Deserialize, Serialize};
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

/// Thread-safe audit logger that writes to a JSONL file
pub struct AuditLogger {
    writer: Mutex<BufWriter<File>>,
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
            writer: Mutex::new(BufWriter::with_capacity(8192, file)),
        })
    }

    /// Log an audit entry. This is thread-safe and can be called from any thread.
    pub fn log(&self, entry: AuditEntry) -> std::io::Result<()> {
        let mut writer = self.writer.lock().unwrap();
        serde_json::to_writer(&mut *writer, &entry)?;
        writer.write_all(b"\n")?;
        writer.flush()
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
        let mut content = String::new();
        File::open(&path)
            .unwrap()
            .read_to_string(&mut content)
            .unwrap();

        let lines: Vec<&str> = content.trim().split('\n').collect();
        assert_eq!(lines.len(), 2);

        let entry1: AuditEntry = serde_json::from_str(lines[0]).unwrap();
        assert_eq!(entry1.timestamp_us, 1000);

        let entry2: AuditEntry = serde_json::from_str(lines[1]).unwrap();
        assert_eq!(entry2.timestamp_us, 2000);
    }
}
