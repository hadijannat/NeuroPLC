#[cfg(feature = "opcua")]
mod enabled {
    use opcua::client::prelude::*;
    use opcua::types::{
        AttributeId, DataValue, NodeId, QualifiedName, ReadValueId, StatusCode, TimestampsToReturn,
        UAString, Variant,
    };
    use std::env;

    const TARGET_NAMESPACE: &str = "urn:neuroplc:opcua";

    pub fn main() -> Result<(), Box<dyn std::error::Error>> {
        let endpoint = env::args()
            .nth(1)
            .or_else(|| env::var("OPCUA_ENDPOINT").ok())
            .unwrap_or_else(|| "opc.tcp://127.0.0.1:4840".to_string());

        let mut client = ClientBuilder::new()
            .application_name("NeuroPLC OPC UA Smoke")
            .application_uri("urn:neuroplc:opcua-smoke")
            .create_sample_keypair(true)
            .trust_server_certs(false)
            .session_retry_limit(1)
            .client()
            .ok_or("failed to build opcua client")?;

        let endpoint_desc: EndpointDescription = (
            endpoint.as_str(),
            "None",
            MessageSecurityMode::None,
            UserTokenPolicy::anonymous(),
        )
            .into();

        let session = client.connect_to_endpoint(endpoint_desc, IdentityToken::Anonymous)?;
        let mut session = session.write();

        let namespaces = read_namespace_array(&mut session)?;
        let ns_index = namespaces
            .iter()
            .position(|ns| ns == TARGET_NAMESPACE)
            .ok_or("target namespace not found")? as u16;

        let value = read_value(&mut session, NodeId::new(ns_index, "MotorSpeedRPM"))?;
        let variant = value.value.ok_or("missing value")?;
        match variant {
            Variant::Double(_) | Variant::Float(_) => {
                println!("OPC UA smoke ok: MotorSpeedRPM read successfully");
                Ok(())
            }
            other => Err(format!("unexpected value type: {other:?}").into()),
        }
    }

    fn read_namespace_array(session: &mut Session) -> Result<Vec<String>, StatusCode> {
        let value = read_value(session, NodeId::new(0u16, 2255u32))?;
        let variant = value.value.ok_or(StatusCode::BadUnexpectedError)?;
        match variant {
            Variant::Array(arr) => Ok(arr
                .values
                .into_iter()
                .filter_map(|value| match value {
                    Variant::String(s) => Some(s.to_string()),
                    _ => None,
                })
                .collect()),
            Variant::String(s) => Ok(vec![s.to_string()]),
            _ => Err(StatusCode::BadUnexpectedError),
        }
    }

    fn read_value(session: &mut Session, node_id: NodeId) -> Result<DataValue, StatusCode> {
        let read_value = ReadValueId {
            node_id,
            attribute_id: AttributeId::Value as u32,
            index_range: UAString::null(),
            data_encoding: QualifiedName::null(),
        };
        let mut values = session.read(&[read_value], TimestampsToReturn::Both, 0.0)?;
        values.pop().ok_or(StatusCode::BadUnexpectedError)
    }
}

#[cfg(feature = "opcua")]
fn main() -> Result<(), Box<dyn std::error::Error>> {
    enabled::main()
}

#[cfg(not(feature = "opcua"))]
fn main() {
    eprintln!("opcua_smoke requires the opcua feature");
}
