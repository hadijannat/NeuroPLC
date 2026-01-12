#![cfg(feature = "opcua")]

use core_spine::{StateExchange, TimeBase};
use log::{info, warn};
use opcua::server::prelude::*;
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;

#[derive(Clone, Debug)]
pub struct OpcuaConfig {
    pub endpoint: String,
    pub update_interval: Duration,
}

impl Default for OpcuaConfig {
    fn default() -> Self {
        Self {
            endpoint: "opc.tcp://0.0.0.0:4840".to_string(),
            update_interval: Duration::from_millis(200),
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

    let mut server_config = ServerBuilder::new_anonymous("NeuroPLC OPC UA")
        .application_uri("urn:neuroplc:opcua")
        .product_uri("urn:neuroplc:opcua")
        .config();

    server_config.create_sample_keypair = true;

    server_config.tcp_config.host = host;
    server_config.tcp_config.port = port;

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

        let speed_id = NodeId::new(ns, "MotorSpeedRPM");
        let temp_id = NodeId::new(ns, "MotorTemperatureC");
        let pressure_id = NodeId::new(ns, "SystemPressureBar");
        let jitter_id = NodeId::new(ns, "CycleJitterUs");
        let timestamp_id = NodeId::new(ns, "TimestampUs");
        let agent_target_id = NodeId::new(ns, "AgentTargetRPM");
        let agent_conf_id = NodeId::new(ns, "AgentConfidence");

        let variables = vec![
            VariableBuilder::new(&speed_id, "MotorSpeedRPM", "MotorSpeedRPM")
                .data_type(DataTypeId::Double)
                .value(0.0)
                .build(),
            VariableBuilder::new(&temp_id, "MotorTemperatureC", "MotorTemperatureC")
                .data_type(DataTypeId::Double)
                .value(0.0)
                .build(),
            VariableBuilder::new(&pressure_id, "SystemPressureBar", "SystemPressureBar")
                .data_type(DataTypeId::Double)
                .value(0.0)
                .build(),
            VariableBuilder::new(&jitter_id, "CycleJitterUs", "CycleJitterUs")
                .data_type(DataTypeId::UInt32)
                .value(0u32)
                .build(),
            VariableBuilder::new(&timestamp_id, "TimestampUs", "TimestampUs")
                .data_type(DataTypeId::UInt64)
                .value(0u64)
                .build(),
            VariableBuilder::new(&agent_target_id, "AgentTargetRPM", "AgentTargetRPM")
                .data_type(DataTypeId::Double)
                .value(0.0)
                .build(),
            VariableBuilder::new(&agent_conf_id, "AgentConfidence", "AgentConfidence")
                .data_type(DataTypeId::Double)
                .value(0.0)
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
