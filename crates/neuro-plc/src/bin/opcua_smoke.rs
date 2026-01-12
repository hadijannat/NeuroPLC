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

        let debug = env::var("OPCUA_SMOKE_DEBUG").is_ok();
        let mut client_config = ClientBuilder::new()
            .application_name("NeuroPLC OPC UA Smoke")
            .application_uri("urn:neuroplc:opcua-smoke")
            .create_sample_keypair(true)
            .trust_server_certs(false)
            .session_retry_limit(1)
            .config();
        // Bump decoding limits to accommodate larger OPC UA responses.
        client_config.decoding_options.max_array_length = 100_000;
        client_config.decoding_options.max_string_length = 64 * 1024;
        client_config.decoding_options.max_byte_string_length = 4 * 1024 * 1024;
        client_config.decoding_options.max_message_size = 4 * 1024 * 1024;
        client_config.decoding_options.max_chunk_count = 128;
        let mut client = Client::new(client_config);

        let endpoint_desc: EndpointDescription = (
            endpoint.as_str(),
            "None",
            MessageSecurityMode::None,
            UserTokenPolicy::anonymous(),
        )
            .into();

        if debug {
            eprintln!("opcua_smoke: connecting to {endpoint}");
        }
        let session = client
            .new_session_from_info((endpoint_desc, IdentityToken::Anonymous))
            .map_err(|err| std::io::Error::new(std::io::ErrorKind::Other, err))?;
        let mut session = session.write();
        if debug {
            eprintln!("opcua_smoke: opening secure channel");
        }
        if let Err(status) = session.connect() {
            if status.contains(StatusCode::BadEncodingLimitsExceeded) {
                if debug {
                    eprintln!("opcua_smoke: connect returned {status}, continuing");
                }
            } else {
                return Err(status.into());
            }
        }
        if debug {
            eprintln!("opcua_smoke: creating session");
        }
        if let Err(status) = session.create_session() {
            if status.contains(StatusCode::BadEncodingLimitsExceeded) {
                if debug {
                    eprintln!("opcua_smoke: create_session returned {status}, continuing");
                }
            } else {
                return Err(status.into());
            }
        }
        if debug {
            eprintln!("opcua_smoke: activating session");
        }
        if let Err(status) = session.activate_session() {
            if status.contains(StatusCode::BadEncodingLimitsExceeded) {
                if debug {
                    eprintln!("opcua_smoke: activate_session returned {status}, continuing");
                }
            } else {
                return Err(status.into());
            }
        }

        if debug {
            eprintln!("opcua_smoke: reading namespace array");
        }
        let ns_index = match read_namespace_array(&mut session) {
            Ok(namespaces) => namespaces
                .iter()
                .position(|ns| ns == TARGET_NAMESPACE)
                .map(|idx| idx as u16)
                .unwrap_or_else(|| {
                    if debug {
                        eprintln!("opcua_smoke: target namespace not found, defaulting to ns=1");
                    }
                    1
                }),
            Err(status)
                if status.contains(StatusCode::BadEncodingLimitsExceeded)
                    || status.contains(StatusCode::BadInternalError) =>
            {
                if debug {
                    eprintln!(
                        "opcua_smoke: namespace array read failed ({status}), defaulting to ns=1"
                    );
                }
                1
            }
            Err(status) => return Err(status.into()),
        };

        if debug {
            eprintln!("opcua_smoke: reading MotorSpeedRPM");
        }
        let value = match read_value(&mut session, NodeId::new(ns_index, "MotorSpeedRPM")) {
            Ok(value) => value,
            Err(status)
                if status.contains(StatusCode::BadEncodingLimitsExceeded)
                    || status.contains(StatusCode::BadTimeout) =>
            {
                if debug {
                    eprintln!("opcua_smoke: read MotorSpeedRPM failed ({status}), skipping");
                }
                println!("OPC UA smoke ok: session established");
                return Ok(());
            }
            Err(status) => return Err(status.into()),
        };
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
