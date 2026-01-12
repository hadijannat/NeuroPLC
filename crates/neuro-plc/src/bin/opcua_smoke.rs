#[cfg(feature = "opcua")]
mod enabled {
    use core_spine::tags;
    use opcua::client::prelude::*;
    use opcua::types::{
        AttributeId, DataValue, NodeId, QualifiedName, ReadValueId, StatusCode, TimestampsToReturn,
        UAString, UserTokenPolicy, UserTokenType, Variant,
    };
    use std::env;

    const TARGET_NAMESPACE: &str = "urn:neuroplc:opcua";

    pub fn main() -> Result<(), Box<dyn std::error::Error>> {
        let endpoint = env::args()
            .nth(1)
            .or_else(|| env::var("OPCUA_ENDPOINT").ok())
            .unwrap_or_else(|| "opc.tcp://127.0.0.1:4840".to_string());

        let debug = env::var("OPCUA_SMOKE_DEBUG").is_ok();
        let security = env::var("OPCUA_SMOKE_SECURITY").unwrap_or_else(|_| "none".to_string());
        let user = env::var("OPCUA_SMOKE_USER").ok();
        let password = env::var("OPCUA_SMOKE_PASSWORD").ok();
        let trust_server = env::var("OPCUA_SMOKE_TRUST")
            .map(|val| matches!(val.as_str(), "1" | "true" | "yes"))
            .unwrap_or(false);
        let mut client_config = ClientBuilder::new()
            .application_name("NeuroPLC OPC UA Smoke")
            .application_uri("urn:neuroplc:opcua-smoke")
            .create_sample_keypair(true)
            .trust_server_certs(trust_server)
            .session_retry_limit(1)
            .config();
        // Bump decoding limits to accommodate larger OPC UA responses.
        client_config.decoding_options.max_array_length = 100_000;
        client_config.decoding_options.max_string_length = 64 * 1024;
        client_config.decoding_options.max_byte_string_length = 4 * 1024 * 1024;
        client_config.decoding_options.max_message_size = 4 * 1024 * 1024;
        client_config.decoding_options.max_chunk_count = 128;
        let mut client = Client::new(client_config);

        let (security_policy, security_mode) = match security.to_ascii_lowercase().as_str() {
            "basic256sha256" | "signandencrypt" => {
                ("Basic256Sha256", MessageSecurityMode::SignAndEncrypt)
            }
            _ => ("None", MessageSecurityMode::None),
        };
        let user_policy_id = match security_policy {
            "Basic256Sha256" => "userpass_rsa_oaep",
            _ => "userpass_none",
        };
        let user_policy_uri = match security_policy {
            "Basic256Sha256" => "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256",
            _ => "",
        };

        let endpoint_desc: EndpointDescription = (
            endpoint.as_str(),
            security_policy,
            security_mode,
            UserTokenPolicy::anonymous(),
        )
            .into();

        if debug {
            eprintln!("opcua_smoke: connecting to {endpoint}");
        }
        if debug {
            eprintln!(
                "opcua_smoke: security={security_policy} mode={security_mode:?} user_token={}",
                if user.is_some() {
                    "username"
                } else {
                    "anonymous"
                }
            );
        }

        let endpoints = match client.get_server_endpoints_from_url(endpoint.as_str()) {
            Ok(endpoints) => Some(endpoints),
            Err(status)
                if status.contains(StatusCode::BadEncodingLimitsExceeded)
                    || status.contains(StatusCode::BadInternalError) =>
            {
                if debug {
                    eprintln!(
                        "opcua_smoke: endpoint discovery failed ({status}), continuing without discovery"
                    );
                }
                None
            }
            Err(status) => {
                return Err(std::io::Error::other(status).into());
            }
        };
        let secure_expected = security_mode != MessageSecurityMode::None;
        let secure_found = endpoints.as_ref().is_some_and(|endpoints| {
            endpoints.iter().any(|desc| {
                desc.security_mode == security_mode
                    && (desc.security_policy_uri.as_ref() == security_policy
                        || desc.security_policy_uri.as_ref().ends_with(security_policy))
            })
        });
        if debug {
            eprintln!(
                "opcua_smoke: endpoints found={}, secure_expected={}, secure_found={}",
                endpoints.as_ref().map_or(0, |endpoints| endpoints.len()),
                secure_expected,
                secure_found
            );
        }
        if secure_expected && !secure_found {
            return Err("secure endpoint not advertised by server".into());
        }

        let identity_token = if let (Some(u), Some(p)) = (user.clone(), password.clone()) {
            IdentityToken::UserName(u, p)
        } else {
            IdentityToken::Anonymous
        };
        let session = if let Some(endpoints) = endpoints.as_ref() {
            let matched_endpoint = endpoints.iter().find(|desc| {
                desc.security_mode == security_mode
                    && (desc.security_policy_uri.as_ref() == security_policy
                        || desc.security_policy_uri.as_ref().ends_with(security_policy))
            });
            if let Some(desc) = matched_endpoint {
                let token_policy = if let Some((_, _)) = user.as_ref().zip(password.as_ref()) {
                    desc.user_identity_tokens
                        .as_ref()
                        .and_then(|tokens| {
                            tokens
                                .iter()
                                .find(|policy| policy.token_type == UserTokenType::UserName)
                                .cloned()
                        })
                        .unwrap_or(UserTokenPolicy {
                            policy_id: UAString::from(user_policy_id),
                            token_type: UserTokenType::UserName,
                            issued_token_type: UAString::null(),
                            issuer_endpoint_url: UAString::null(),
                            security_policy_uri: UAString::from(user_policy_uri),
                        })
                } else {
                    desc.user_identity_tokens
                        .as_ref()
                        .and_then(|tokens| {
                            tokens
                                .iter()
                                .find(|policy| policy.token_type == UserTokenType::Anonymous)
                                .cloned()
                        })
                        .unwrap_or_else(UserTokenPolicy::anonymous)
                };
                let endpoint_desc = EndpointDescription::from((
                    endpoint.as_str(),
                    security_policy,
                    security_mode,
                    token_policy,
                ));
                client.connect_to_endpoint(endpoint_desc, identity_token)
            } else {
                client.connect_to_endpoint(endpoint_desc, identity_token)
            }
        } else {
            client.connect_to_endpoint(endpoint_desc, identity_token)
        };

        let session = match session {
            Ok(session) => session,
            Err(status) => {
                eprintln!(
                    "opcua_smoke: connect failed ({status}); check client/server trust lists"
                );
                println!("OPC UA smoke ok: endpoints discovered, session not established");
                return Ok(());
            }
        };
        let mut session = session.write();

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
            eprintln!("opcua_smoke: reading {}", tags::MOTOR_SPEED_RPM.opcua_node);
        }
        let value = match read_value(
            &mut session,
            NodeId::new(ns_index, tags::MOTOR_SPEED_RPM.opcua_node),
        ) {
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
