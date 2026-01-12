use crate::infra::audit::{hash_bytes, hash_str, AuditEventType, AuditLogger};
#[cfg(feature = "opcua")]
use crate::integrations::opcua_server::{run_opcua, OpcuaConfig};
#[cfg(feature = "rerun")]
use crate::integrations::rerun_viz::{run_rerun, RerunConfig};
use crate::runtime::config::RuntimeConfig;
use crate::runtime::logging::init_tracing;
use crate::runtime::telemetry;
use core_spine::{
    ControlConfig, CycleStats, IronThread, MachineIO, SimulatedMotor, StateExchange, TimeBase,
};
use neuro_io::auth::AuthConfig;
use neuro_io::bridge::{run_bridge, BridgeConfig, WireProtocol};
use neuro_io::hal_modbus::ModbusMotor;
use neuro_io::tls::TlsConfig;
use std::path::PathBuf;
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;
use tracing::{info, warn};

enum NeuroPlcMotor {
    Simulated(SimulatedMotor),
    Modbus(ModbusMotor),
}

impl MachineIO for NeuroPlcMotor {
    fn step(&mut self, dt_s: f64) {
        match self {
            Self::Simulated(m) => m.step(dt_s),
            Self::Modbus(m) => m.step(dt_s),
        }
    }

    fn read_speed(&self) -> f64 {
        match self {
            Self::Simulated(m) => m.read_speed(),
            Self::Modbus(m) => m.read_speed(),
        }
    }

    fn read_temperature(&self) -> f64 {
        match self {
            Self::Simulated(m) => m.read_temperature(),
            Self::Modbus(m) => m.read_temperature(),
        }
    }

    fn read_pressure(&self) -> f64 {
        match self {
            Self::Simulated(m) => m.read_pressure(),
            Self::Modbus(m) => m.read_pressure(),
        }
    }

    fn write_speed(&mut self, rpm: f64) {
        match self {
            Self::Simulated(m) => m.write_speed(rpm),
            Self::Modbus(m) => m.write_speed(rpm),
        }
    }

    fn cycle_stats(&self) -> CycleStats {
        match self {
            Self::Simulated(m) => m.cycle_stats(),
            Self::Modbus(m) => m.cycle_stats(),
        }
    }

    fn is_healthy(&self) -> bool {
        match self {
            Self::Simulated(m) => m.is_healthy(),
            Self::Modbus(m) => m.is_healthy(),
        }
    }
}

pub fn run_from_args() {
    let config = RuntimeConfig::from_env();
    if config.show_help {
        RuntimeConfig::print_help();
        return;
    }
    run(config);
}

pub fn run(config: RuntimeConfig) {
    // Initialize tracing
    init_tracing(config.json_logs);

    // Initialize metrics
    telemetry::init();

    // Start metrics server if enabled
    let metrics_enabled = config.metrics_addr.is_some();
    let _metrics_handle = telemetry::start_metrics_server(&config.metrics_addr);

    let control_config = ControlConfig::default();
    let exchange = Arc::new(StateExchange::new(
        control_config.recommendation_timeout.as_micros() as u64,
    ));
    let timebase = TimeBase::new();

    // Initialize audit logger if enabled
    let audit_logger = init_audit_logger(config.audit_path.as_ref());

    // Log startup
    if let Some(ref logger) = audit_logger {
        let config_hash = hash_runtime_config(&config);
        let binary_hash = current_binary_hash();
        let cortex_hash = cortex_manifest_hash();
        let _ = logger.log_event(
            timebase.now_us(),
            timebase.unix_us(),
            AuditEventType::SystemStart,
            serde_json::json!({
                "version": env!("CARGO_PKG_VERSION"),
                "bridge_enabled": config.bridge_enabled,
                "metrics_enabled": metrics_enabled,
                "config_hash": config_hash,
                "binary_sha256": binary_hash,
                "cortex_manifest_sha256": cortex_hash,
            }),
        );
    }

    let stop = Arc::new(AtomicBool::new(false));
    let metrics_updater = if metrics_enabled {
        Some(telemetry::start_metrics_updater(
            Arc::clone(&exchange),
            Arc::clone(&stop),
        ))
    } else {
        None
    };

    let exchange_iron = Arc::clone(&exchange);
    let stop_iron = Arc::clone(&stop);
    let timebase_iron = timebase;
    let control_config_iron = control_config.clone();
    let modbus_addr = config.modbus_addr.clone();

    info!(
        cycle_time_ms = control_config.cycle_time.as_millis(),
        max_speed_rpm = control_config.safety_limits.max_speed_rpm,
        max_temp_c = control_config.safety_limits.max_temp_c,
        "Starting IronThread control loop"
    );

    let iron_handle = thread::spawn(move || {
        let io = if let Some(addr) = modbus_addr {
            info!(addr = %addr, "Connecting to Modbus HAL");
            let motor = ModbusMotor::new(&addr).expect("Failed to initialize Modbus HAL client");
            NeuroPlcMotor::Modbus(motor)
        } else {
            NeuroPlcMotor::Simulated(SimulatedMotor::new())
        };

        let mut iron = IronThread::new(io, control_config_iron, exchange_iron, timebase_iron);
        iron.run(&stop_iron);
        iron.stats().clone()
    });

    let bridge_handle = if config.bridge_enabled {
        let exchange_bridge = Arc::clone(&exchange);
        let stop_bridge = Arc::clone(&stop);
        let timebase_bridge = timebase;
        let bridge_config = build_bridge_config(&config);
        info!(addr = %bridge_config.bind_addr, "Starting bridge");
        Some(thread::spawn(move || {
            run_bridge(exchange_bridge, timebase_bridge, bridge_config, stop_bridge);
        }))
    } else {
        info!("Bridge disabled");
        None
    };

    #[cfg(feature = "opcua")]
    let opcua_handle = if config.opcua_enabled {
        let opcua_config = OpcuaConfig {
            endpoint: config.opcua_endpoint.clone(),
            secure_only: config.opcua_secure_only,
            allow_anonymous: config.opcua_allow_anonymous,
            username: config.opcua_user.clone(),
            password: config.opcua_password.clone(),
            pki_dir: config.opcua_pki_dir.clone(),
            create_sample_keypair: config.opcua_create_sample_keypair,
            allow_write: config.opcua_allow_write,
            ..Default::default()
        };
        info!(endpoint = %opcua_config.endpoint, "Starting OPC UA server");
        Some(run_opcua(
            Arc::clone(&exchange),
            timebase,
            Arc::clone(&stop),
            opcua_config,
        ))
    } else {
        None
    };

    #[cfg(feature = "rerun")]
    let rerun_handle = if config.rerun_enabled {
        let rerun_config = RerunConfig {
            save_path: config.rerun_save_path.map(PathBuf::from),
            ..Default::default()
        };
        info!("Starting Rerun visualization");
        run_rerun(
            Arc::clone(&exchange),
            timebase,
            Arc::clone(&stop),
            rerun_config,
        )
    } else {
        None
    };

    info!("NeuroPLC running. Connect python-cortex to send recommendations.");

    if let Some(seconds) = config.run_seconds {
        info!(seconds, "Running for limited duration");
        thread::sleep(Duration::from_secs(seconds));
        stop.store(true, std::sync::atomic::Ordering::Relaxed);

        let stats = iron_handle.join().unwrap();
        if let Some(handle) = bridge_handle {
            let _ = handle.join();
        }
        if let Some(handle) = metrics_updater {
            let _ = handle.join();
        }
        #[cfg(feature = "opcua")]
        if let Some(handle) = opcua_handle {
            let _ = handle.join();
        }
        #[cfg(feature = "rerun")]
        if let Some(handle) = rerun_handle {
            let _ = handle.join();
        }

        info!(
            cycles_executed = stats.cycles_executed,
            cycles_missed = stats.cycles_missed,
            safety_rejections = stats.safety_rejections,
            max_jitter_us = stats.max_jitter_us,
            timing_violations = stats.timing_violations,
            "Run complete"
        );

        // Log shutdown
        if let Some(ref logger) = audit_logger {
            let _ = logger.log_event(
                timebase.now_us(),
                timebase.unix_us(),
                AuditEventType::SystemShutdown,
                serde_json::json!({
                    "cycles_executed": stats.cycles_executed,
                    "cycles_missed": stats.cycles_missed,
                    "safety_rejections": stats.safety_rejections,
                    "timing_violations": stats.timing_violations,
                }),
            );
        }
    } else {
        let _ = iron_handle.join();
        if let Some(handle) = bridge_handle {
            let _ = handle.join();
        }
        if let Some(handle) = metrics_updater {
            let _ = handle.join();
        }
        #[cfg(feature = "opcua")]
        if let Some(handle) = opcua_handle {
            let _ = handle.join();
        }
        #[cfg(feature = "rerun")]
        if let Some(handle) = rerun_handle {
            let _ = handle.join();
        }
    }
}

fn build_bridge_config(config: &RuntimeConfig) -> BridgeConfig {
    let wire_protocol = WireProtocol::parse(&config.bridge_protocol).unwrap_or_else(|| {
        warn!(
            protocol = %config.bridge_protocol,
            "Unknown protocol, defaulting to json"
        );
        WireProtocol::JsonLines
    });

    BridgeConfig {
        bind_addr: config.bind_addr.clone(),
        tls: TlsConfig {
            enabled: config.tls_cert.is_some() && config.tls_key.is_some(),
            cert_path: config.tls_cert.clone().unwrap_or_default(),
            key_path: config.tls_key.clone().unwrap_or_default(),
            require_client_auth: config.tls_require_client_cert,
            client_ca_path: config.tls_client_ca.clone().unwrap_or_default(),
        },
        auth: AuthConfig {
            enabled: config.auth_secret.is_some(),
            secret: config.auth_secret.clone().unwrap_or_default().into_bytes(),
            max_age_secs: config.auth_max_age_secs,
            issuer: config.auth_issuer.clone(),
            audience: config.auth_audience.clone(),
            required_scope: config.auth_scope.clone(),
            ..Default::default()
        },
        require_handshake: config.bridge_require_handshake,
        wire_protocol,
        ..Default::default()
    }
}

fn init_audit_logger(audit_path: Option<&PathBuf>) -> Option<Arc<AuditLogger>> {
    audit_path.map(|path| match AuditLogger::new(path) {
        Ok(logger) => {
            info!(path = %path.display(), "Audit logging enabled");
            Arc::<AuditLogger>::new(logger)
        }
        Err(e) => {
            warn!(error = %e, path = %path.display(), "Failed to initialize audit logger");
            panic!("Audit logging requested but failed to initialize: {}", e);
        }
    })
}

fn hash_runtime_config(config: &RuntimeConfig) -> String {
    let mut summary = serde_json::Map::new();
    summary.insert("bind_addr".to_string(), config.bind_addr.clone().into());
    summary.insert(
        "bridge_enabled".to_string(),
        serde_json::Value::Bool(config.bridge_enabled),
    );
    summary.insert(
        "metrics_addr".to_string(),
        config.metrics_addr.clone().into(),
    );
    summary.insert(
        "tls_enabled".to_string(),
        serde_json::Value::Bool(config.tls_cert.is_some() && config.tls_key.is_some()),
    );
    summary.insert(
        "tls_require_client_cert".to_string(),
        serde_json::Value::Bool(config.tls_require_client_cert),
    );
    summary.insert(
        "auth_enabled".to_string(),
        serde_json::Value::Bool(config.auth_secret.is_some()),
    );
    summary.insert(
        "bridge_require_handshake".to_string(),
        serde_json::Value::Bool(config.bridge_require_handshake),
    );
    summary.insert(
        "bridge_protocol".to_string(),
        config.bridge_protocol.clone().into(),
    );
    summary.insert(
        "auth_max_age_secs".to_string(),
        serde_json::Value::Number(config.auth_max_age_secs.into()),
    );
    summary.insert("auth_issuer".to_string(), config.auth_issuer.clone().into());
    summary.insert(
        "auth_audience".to_string(),
        config.auth_audience.clone().into(),
    );
    summary.insert("auth_scope".to_string(), config.auth_scope.clone().into());
    summary.insert("modbus_addr".to_string(), config.modbus_addr.clone().into());

    #[cfg(feature = "opcua")]
    {
        summary.insert(
            "opcua_enabled".to_string(),
            serde_json::Value::Bool(config.opcua_enabled),
        );
        summary.insert(
            "opcua_endpoint".to_string(),
            config.opcua_endpoint.clone().into(),
        );
        summary.insert(
            "opcua_secure_only".to_string(),
            serde_json::Value::Bool(config.opcua_secure_only),
        );
        summary.insert(
            "opcua_allow_anonymous".to_string(),
            serde_json::Value::Bool(config.opcua_allow_anonymous),
        );
        summary.insert(
            "opcua_user_configured".to_string(),
            serde_json::Value::Bool(config.opcua_user.is_some()),
        );
        summary.insert(
            "opcua_allow_write".to_string(),
            serde_json::Value::Bool(config.opcua_allow_write),
        );
        summary.insert(
            "opcua_pki_dir".to_string(),
            config.opcua_pki_dir.clone().into(),
        );
        summary.insert(
            "opcua_create_sample_keypair".to_string(),
            serde_json::Value::Bool(config.opcua_create_sample_keypair),
        );
    }

    #[cfg(feature = "rerun")]
    {
        summary.insert(
            "rerun_enabled".to_string(),
            serde_json::Value::Bool(config.rerun_enabled),
        );
        summary.insert(
            "rerun_save_path".to_string(),
            config.rerun_save_path.clone().into(),
        );
    }

    hash_str(&serde_json::Value::Object(summary).to_string())
}

fn current_binary_hash() -> Option<String> {
    let path = std::env::current_exe().ok()?;
    let bytes = std::fs::read(path).ok()?;
    Some(hash_bytes(&bytes))
}

fn cortex_manifest_hash() -> Option<String> {
    let path = std::path::Path::new("python-cortex/pyproject.toml");
    let bytes = std::fs::read(path).ok()?;
    Some(hash_bytes(&bytes))
}
