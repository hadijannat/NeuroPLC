//! Protobuf protocol definitions (optional).
//!
//! Enable with the `proto` feature to generate types from `proto/neuroplc.proto`.

#[cfg(feature = "proto")]
pub mod proto {
    include!(concat!(env!("OUT_DIR"), "/neuroplc.rs"));
}

#[cfg(feature = "proto")]
use crate::protocol::{ProtocolVersion, RecommendationMsg};

#[cfg(feature = "proto")]
impl From<ProtocolVersion> for proto::ProtocolVersion {
    fn from(value: ProtocolVersion) -> Self {
        Self {
            major: value.major as u32,
            minor: value.minor as u32,
        }
    }
}

#[cfg(feature = "proto")]
impl From<proto::ProtocolVersion> for ProtocolVersion {
    fn from(value: proto::ProtocolVersion) -> Self {
        Self {
            major: value.major as u8,
            minor: value.minor as u8,
        }
    }
}

#[cfg(feature = "proto")]
impl From<RecommendationMsg> for proto::Recommendation {
    fn from(value: RecommendationMsg) -> Self {
        Self {
            protocol_version: Some(value.protocol_version.into()),
            sequence: value.sequence,
            issued_at_unix_us: value.issued_at_unix_us,
            ttl_ms: value.ttl_ms,
            target_speed_rpm: value.target_speed_rpm,
            confidence: value.confidence,
            reasoning_hash: value.reasoning_hash,
            client_unix_us: value.client_unix_us,
            auth_token: value.auth_token,
        }
    }
}
