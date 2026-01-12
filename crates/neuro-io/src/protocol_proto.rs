//! Protobuf protocol definitions (optional).
//!
//! Enable with the `proto` feature to generate types from `proto/neuroplc.proto`.

#[cfg(feature = "proto")]
pub mod proto {
    include!(concat!(env!("OUT_DIR"), "/neuroplc.rs"));
}

#[cfg(feature = "proto")]
use crate::protocol::{HelloMsg, IncomingMessage, ProtocolVersion, RecommendationMsg};

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

#[cfg(feature = "proto")]
impl From<HelloMsg> for proto::Hello {
    fn from(value: HelloMsg) -> Self {
        Self {
            protocol_version: Some(value.protocol_version.into()),
            capabilities: value.capabilities,
            client_id: value.client_id,
        }
    }
}

#[cfg(feature = "proto")]
impl TryFrom<proto::Hello> for HelloMsg {
    type Error = ();

    fn try_from(value: proto::Hello) -> Result<Self, Self::Error> {
        let protocol_version = value
            .protocol_version
            .map(ProtocolVersion::from)
            .unwrap_or_default();
        Ok(Self {
            msg_type: "hello".to_string(),
            protocol_version,
            capabilities: value.capabilities,
            client_id: value.client_id,
        })
    }
}

#[cfg(feature = "proto")]
impl TryFrom<proto::Recommendation> for RecommendationMsg {
    type Error = ();

    fn try_from(value: proto::Recommendation) -> Result<Self, Self::Error> {
        let protocol_version = value
            .protocol_version
            .map(ProtocolVersion::from)
            .unwrap_or_default();
        Ok(Self {
            msg_type: "recommendation".to_string(),
            protocol_version,
            sequence: value.sequence,
            target_speed_rpm: value.target_speed_rpm,
            confidence: value.confidence,
            reasoning_hash: value.reasoning_hash,
            issued_at_unix_us: value.issued_at_unix_us,
            ttl_ms: value.ttl_ms,
            client_unix_us: value.client_unix_us,
            auth_token: value.auth_token,
        })
    }
}

#[cfg(feature = "proto")]
impl TryFrom<proto::WireMessage> for IncomingMessage {
    type Error = ();

    fn try_from(value: proto::WireMessage) -> Result<Self, Self::Error> {
        match value.payload {
            Some(proto::wire_message::Payload::Hello(msg)) => {
                HelloMsg::try_from(msg).map(IncomingMessage::Hello)
            }
            Some(proto::wire_message::Payload::Recommendation(msg)) => {
                RecommendationMsg::try_from(msg).map(IncomingMessage::Recommendation)
            }
            _ => Err(()),
        }
    }
}
