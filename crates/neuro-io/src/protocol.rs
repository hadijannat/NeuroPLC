use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize)]
pub struct StateMsg {
    #[serde(rename = "type")]
    pub msg_type: &'static str,
    pub timestamp_us: u64,
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
    pub target_speed_rpm: Option<f64>,
    pub confidence: f32,
    pub reasoning_hash: String,
    #[allow(dead_code)]
    pub client_unix_us: Option<u64>,
    #[allow(dead_code)]
    pub auth_token: Option<String>,
}

#[derive(Debug)]
pub enum IncomingMessage {
    Recommendation(RecommendationMsg),
}

impl IncomingMessage {
    pub fn parse(line: &str) -> Option<Self> {
        let parsed: RecommendationMsg = serde_json::from_str(line).ok()?;
        if parsed.msg_type == "recommendation" {
            Some(IncomingMessage::Recommendation(parsed))
        } else {
            None
        }
    }
}
