#![cfg(feature = "opcua")]

use core_spine::{tags, StateExchange, TimeBase};
use opcua::server::address_space::{AccessLevel, UserAccessLevel};
use opcua::server::config::{ServerEndpoint, ServerUserToken, ANONYMOUS_USER_TOKEN_ID};
use opcua::server::prelude::*;
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;
use tracing::{info, warn};

#[derive(Clone, Debug)]
pub struct OpcuaConfig {
    pub endpoint: String,
    pub update_interval: Duration,
    pub secure_only: bool,
    pub allow_anonymous: bool,
    pub username: Option<String>,
    pub password: Option<String>,
    pub pki_dir: String,
    pub create_sample_keypair: bool,
    pub allow_write: bool,
}

impl Default for OpcuaConfig {
    fn default() -> Self {
        Self {
            endpoint: "opc.tcp://0.0.0.0:4840".to_string(),
            update_interval: Duration::from_millis(200),
            secure_only: false,
            allow_anonymous: true,
            username: None,
            password: None,
            pki_dir: "./pki-server".to_string(),
            create_sample_keypair: true,
            allow_write: false,
        }
    }
}

pub fn run_opcua(
    exchange: Arc<StateExchange>,
    timebase: TimeBase,
    stop: Arc<AtomicBool>,
    config: OpcuaConfig,
) -> thread::JoinHandle<()> {
    let (host, port) = parse_endpoint(&config.endpoint);

    let mut anon_tokens = Vec::new();
    if config.allow_anonymous {
        anon_tokens.push(ANONYMOUS_USER_TOKEN_ID.to_string());
    }

    let mut secure_tokens = Vec::new();
    if config.allow_anonymous {
        secure_tokens.push(ANONYMOUS_USER_TOKEN_ID.to_string());
    }

    if let (Some(user), Some(_pass)) = (config.username.as_ref(), config.password.as_ref()) {
        secure_tokens.push(user.clone());
    }

    if secure_tokens.is_empty() && anon_tokens.is_empty() {
        warn!("OPC UA configured without any user tokens; enabling anonymous access");
        anon_tokens.push(ANONYMOUS_USER_TOKEN_ID.to_string());
        secure_tokens.push(ANONYMOUS_USER_TOKEN_ID.to_string());
    }

    let endpoints = if config.secure_only {
        vec![(
            "basic256sha256_sign_encrypt",
            ServerEndpoint::new_basic256sha256_sign_encrypt("/", &secure_tokens),
        )]
    } else {
        vec![
            ("none", ServerEndpoint::new_none("/", &anon_tokens)),
            (
                "basic256sha256_sign_encrypt",
                ServerEndpoint::new_basic256sha256_sign_encrypt("/", &secure_tokens),
            ),
        ]
    };

    let mut builder = ServerBuilder::new();
    if let (Some(user), Some(pass)) = (config.username.as_ref(), config.password.as_ref()) {
        builder = builder.user_token(user, ServerUserToken::user_pass(user, pass));
    }

    let server_config = builder
        .application_name("NeuroPLC OPC UA")
        .application_uri("urn:neuroplc:opcua")
        .product_uri("urn:neuroplc:opcua")
        .create_sample_keypair(config.create_sample_keypair)
        .pki_dir(&config.pki_dir)
        .host_and_port(host.clone(), port)
        .max_array_length(100_000)
        .max_string_length(64 * 1024)
        .max_byte_string_length(4 * 1024 * 1024)
        .max_message_size(4 * 1024 * 1024)
        .max_chunk_count(128)
        .endpoints(endpoints)
        .discovery_urls(vec!["/".to_string()])
        .config();

    let server = Server::new(server_config);
    let address_space = server.address_space();

    let (ns, folder_id, nodes) = {
        let mut space = address_space.write();
        let ns = space
            .register_namespace("urn:neuroplc:opcua")
            .unwrap_or_else(|_| space.default_namespace());
        let objects = NodeId::objects_folder_id();
        let folder_id = space
            .add_folder("NeuroPLC", "NeuroPLC", &objects)
            .unwrap_or_else(|_| NodeId::objects_folder_id());

        let access_level = || {
            if config.allow_write {
                AccessLevel::CURRENT_READ | AccessLevel::CURRENT_WRITE
            } else {
                AccessLevel::CURRENT_READ
            }
        };
        let user_access_level = || {
            if config.allow_write {
                UserAccessLevel::CURRENT_READ | UserAccessLevel::CURRENT_WRITE
            } else {
                UserAccessLevel::CURRENT_READ
            }
        };

        let speed_id = NodeId::new(ns, tags::MOTOR_SPEED_RPM.opcua_node);
        let temp_id = NodeId::new(ns, tags::MOTOR_TEMP_C.opcua_node);
        let pressure_id = NodeId::new(ns, tags::PRESSURE_BAR.opcua_node);
        let jitter_id = NodeId::new(ns, tags::CYCLE_JITTER_US.opcua_node);
        let timestamp_id = NodeId::new(ns, tags::TIMESTAMP_US.opcua_node);
        let safety_state_id = NodeId::new(ns, tags::SAFETY_STATE.opcua_node);
        let agent_target_id = NodeId::new(ns, tags::AGENT_TARGET_RPM.opcua_node);
        let agent_conf_id = NodeId::new(ns, tags::AGENT_CONFIDENCE.opcua_node);

        let variables = vec![
            VariableBuilder::new(
                &speed_id,
                tags::MOTOR_SPEED_RPM.opcua_node,
                tags::MOTOR_SPEED_RPM.opcua_node,
            )
            .data_type(DataTypeId::Double)
            .value(0.0)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &temp_id,
                tags::MOTOR_TEMP_C.opcua_node,
                tags::MOTOR_TEMP_C.opcua_node,
            )
            .data_type(DataTypeId::Double)
            .value(0.0)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &pressure_id,
                tags::PRESSURE_BAR.opcua_node,
                tags::PRESSURE_BAR.opcua_node,
            )
            .data_type(DataTypeId::Double)
            .value(0.0)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &jitter_id,
                tags::CYCLE_JITTER_US.opcua_node,
                tags::CYCLE_JITTER_US.opcua_node,
            )
            .data_type(DataTypeId::UInt32)
            .value(0u32)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &timestamp_id,
                tags::TIMESTAMP_US.opcua_node,
                tags::TIMESTAMP_US.opcua_node,
            )
            .data_type(DataTypeId::UInt64)
            .value(0u64)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &safety_state_id,
                tags::SAFETY_STATE.opcua_node,
                tags::SAFETY_STATE.opcua_node,
            )
            .data_type(DataTypeId::UInt32)
            .value(0u32)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &agent_target_id,
                tags::AGENT_TARGET_RPM.opcua_node,
                tags::AGENT_TARGET_RPM.opcua_node,
            )
            .data_type(DataTypeId::Double)
            .value(0.0)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
            VariableBuilder::new(
                &agent_conf_id,
                tags::AGENT_CONFIDENCE.opcua_node,
                tags::AGENT_CONFIDENCE.opcua_node,
            )
            .data_type(DataTypeId::Double)
            .value(0.0)
            .access_level(access_level())
            .user_access_level(user_access_level())
            .build(),
        ];

        space.add_variables(variables, &folder_id);

        (
            ns,
            folder_id,
            NodeIds {
                speed_id,
                temp_id,
                pressure_id,
                jitter_id,
                timestamp_id,
                safety_state_id,
                agent_target_id,
                agent_conf_id,
            },
        )
    };

    info!("OPC UA server namespace {} folder {:?}", ns, folder_id);

    let server = Arc::new(opcua::sync::RwLock::new(server));
    let server_for_run = Arc::clone(&server);
    let server_for_updates = Arc::clone(&server);
    let address_for_updates = address_space.clone();

    let update_handle = thread::spawn(move || {
        while !stop.load(std::sync::atomic::Ordering::Relaxed) {
            let snapshot = exchange.read_state();
            let rec = exchange.get_recommendation(timebase.now_us());
            let now = DateTime::now();

            let mut space = address_for_updates.write();
            space.set_variable_value(&nodes.speed_id, snapshot.motor_speed_rpm, &now, &now);
            space.set_variable_value(&nodes.temp_id, snapshot.motor_temp_c, &now, &now);
            space.set_variable_value(&nodes.pressure_id, snapshot.pressure_bar, &now, &now);
            space.set_variable_value(&nodes.jitter_id, snapshot.cycle_jitter_us, &now, &now);
            space.set_variable_value(&nodes.timestamp_id, snapshot.timestamp_us, &now, &now);
            space.set_variable_value(
                &nodes.safety_state_id,
                snapshot.safety_state.as_u8() as u32,
                &now,
                &now,
            );

            if let Some(r) = rec {
                if let Some(target) = r.target_speed_rpm {
                    space.set_variable_value(&nodes.agent_target_id, target, &now, &now);
                }
                space.set_variable_value(&nodes.agent_conf_id, r.confidence as f64, &now, &now);
            }

            thread::sleep(config.update_interval);
        }

        warn!("OPC UA server stopping");
        let mut server = server_for_updates.write();
        server.abort();
    });

    thread::spawn(move || {
        Server::run_server(server_for_run);
    });

    update_handle
}

struct NodeIds {
    speed_id: NodeId,
    temp_id: NodeId,
    pressure_id: NodeId,
    jitter_id: NodeId,
    timestamp_id: NodeId,
    safety_state_id: NodeId,
    agent_target_id: NodeId,
    agent_conf_id: NodeId,
}

fn parse_endpoint(endpoint: &str) -> (String, u16) {
    let trimmed = endpoint.trim();
    let without_scheme = trimmed.strip_prefix("opc.tcp://").unwrap_or(trimmed);
    let mut parts = without_scheme.split('/').next().unwrap_or("").split(':');
    let host = parts.next().unwrap_or("0.0.0.0").to_string();
    let port = parts
        .next()
        .and_then(|p| p.parse::<u16>().ok())
        .unwrap_or(4840);
    (host, port)
}
