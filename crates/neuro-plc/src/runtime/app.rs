use crate::infra::audit::{AuditEventType, AuditLogger};
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
use neuro_io::bridge::{run_bridge, BridgeConfig};
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
        let _ = logger.log_event(
            timebase.now_us(),
            timebase.unix_us(),
            AuditEventType::SystemStart,
            serde_json::json!({
                "version": env!("CARGO_PKG_VERSION"),
                "bridge_enabled": config.bridge_enabled,
                "metrics_enabled": metrics_enabled,
            }),
        );
    }

    let stop = Arc::new(AtomicBool::new(false));

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
        let mut opcua_config = OpcuaConfig::default();
        opcua_config.endpoint = config.opcua_endpoint.clone();
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
        let mut rerun_config = RerunConfig::default();
        rerun_config.save_path = config.rerun_save_path.map(PathBuf::from);
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
                }),
            );
        }
    } else {
        let _ = iron_handle.join();
        if let Some(handle) = bridge_handle {
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
    BridgeConfig {
        bind_addr: config.bind_addr.clone(),
        tls: TlsConfig {
            enabled: config.tls_cert.is_some() && config.tls_key.is_some(),
            cert_path: config.tls_cert.clone().unwrap_or_default(),
            key_path: config.tls_key.clone().unwrap_or_default(),
        },
        auth: AuthConfig {
            enabled: config.auth_secret.is_some(),
            secret: config.auth_secret.clone().unwrap_or_default().into_bytes(),
            max_age_secs: config.auth_max_age_secs,
        },
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
