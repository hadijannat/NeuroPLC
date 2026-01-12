use core_spine::tags;
use serde::{Deserialize, Serialize};

pub const STATE_TAGS: &[tags::Tag] = &[
    tags::MOTOR_SPEED_RPM,
    tags::MOTOR_TEMP_C,
    tags::PRESSURE_BAR,
    tags::CYCLE_JITTER_US,
    tags::TIMESTAMP_US,
];

#[derive(Debug, Clone, Copy, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct ProtocolVersion {
    pub major: u8,
    pub minor: u8,
}

impl ProtocolVersion {
    pub const fn v1() -> Self {
        Self { major: 1, minor: 0 }
    }

    pub fn is_supported(&self) -> bool {
        self.major == 1
    }
}

#[derive(Debug, Serialize)]
pub struct StateMsg {
    #[serde(rename = "type")]
    pub msg_type: &'static str,
    pub protocol_version: ProtocolVersion,
    pub sequence: u64,
    pub timestamp_us: u64,
    pub cycle_count: u64,
    pub unix_us: u64,
    pub motor_speed_rpm: f64,
    pub motor_temp_c: f64,
    pub pressure_bar: f64,
    pub cycle_jitter_us: u32,
}

#[derive(Debug, Deserialize)]
pub struct RecommendationMsg {
    #[serde(rename = "type")]
    pub msg_type: String,
    #[serde(default)]
    pub protocol_version: ProtocolVersion,
    #[serde(default)]
    pub sequence: u64,
    pub target_speed_rpm: Option<f64>,
    pub confidence: f32,
    pub reasoning_hash: String,
    #[serde(default)]
    pub issued_at_unix_us: u64,
    #[serde(default)]
    pub ttl_ms: u64,
    #[allow(dead_code)]
    pub client_unix_us: Option<u64>,
    #[allow(dead_code)]
    pub auth_token: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct HelloMsg {
    #[serde(rename = "type")]
    pub msg_type: String,
    #[serde(default)]
    pub protocol_version: ProtocolVersion,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(default)]
    pub client_id: Option<String>,
}

#[derive(Debug)]
pub enum IncomingMessage {
    Hello(HelloMsg),
    Recommendation(RecommendationMsg),
}

impl IncomingMessage {
    pub fn parse(line: &str) -> Option<Self> {
        let value: serde_json::Value = serde_json::from_str(line).ok()?;
        let msg_type = value.get("type")?.as_str()?;
        match msg_type {
            "recommendation" => serde_json::from_value(value)
                .ok()
                .map(IncomingMessage::Recommendation),
            "hello" => serde_json::from_value(value)
                .ok()
                .map(IncomingMessage::Hello),
            _ => None,
        }
    }
}
