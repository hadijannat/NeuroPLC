pub mod auth;
pub mod bridge;
pub mod hal_modbus;
pub mod metrics;
pub mod protocol;
#[cfg(feature = "proto")]
pub mod protocol_proto;
pub mod tls;

pub use auth::{AuthConfig, AuthError, TokenClaims, TokenValidator};
pub use bridge::{run_bridge, BridgeConfig};
pub use hal_modbus::ModbusMotor;
pub use metrics::{init_metrics, serve_metrics};
pub use protocol::{IncomingMessage, ProtocolVersion, RecommendationMsg, StateMsg};
pub use tls::{build_server_config, TlsConfig, TlsError};
