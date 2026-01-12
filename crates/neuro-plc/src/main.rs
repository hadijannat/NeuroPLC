mod audit;
mod auth;
mod bridge;
mod hal_modbus;
mod metrics;
#[cfg(feature = "opcua")]
mod opcua_server;
mod protocol;
#[cfg(feature = "rerun")]
mod rerun_viz;
mod tls;

use audit::{AuditEventType, AuditLogger};
use auth::AuthConfig;
use bridge::{run_bridge, BridgeConfig};
use core_spine::{
    ControlConfig, CycleStats, IronThread, MachineIO, SimulatedMotor, StateExchange, TimeBase,
};
use hal_modbus::ModbusMotor;
use metrics::{init_metrics, serve_metrics};
#[cfg(feature = "opcua")]
use opcua_server::{run_opcua, OpcuaConfig};
#[cfg(feature = "rerun")]
use rerun_viz::{run_rerun, RerunConfig};
use std::path::PathBuf;
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;
use tls::TlsConfig;
use tracing::{info, warn};
use tracing_subscriber::{fmt, prelude::*, EnvFilter};

/// Initialize the tracing subscriber with optional JSON output
fn init_tracing(json_output: bool) {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,neuro_plc=debug,core_spine=debug"));

    if json_output {
        tracing_subscriber::registry()
            .with(filter)
            .with(fmt::layer().json())
            .init();
    } else {
        tracing_subscriber::registry()
            .with(filter)
            .with(fmt::layer().pretty())
            .init();
    }
}

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

fn main() {
    // Parse arguments first to determine log format
    let args: Vec<String> = std::env::args().collect();

    // Configuration variables
    let mut run_seconds: Option<u64> = None;
    let mut bind_addr = "127.0.0.1:7000".to_string();
    let mut bridge_enabled = true;
    let mut json_logs = false;
    let mut metrics_addr: Option<String> = None;
    let mut audit_path: Option<PathBuf> = None;
    let mut tls_cert: Option<String> = None;
    let mut tls_key: Option<String> = None;
    let mut auth_secret: Option<String> = None;
    let mut auth_max_age_secs: u64 = 300;
    let mut modbus_addr: Option<String> = None;

    #[cfg(feature = "opcua")]
    let mut opcua_enabled = false;
    #[cfg(feature = "opcua")]
    let mut opcua_endpoint = "opc.tcp://0.0.0.0:4840".to_string();
    #[cfg(feature = "rerun")]
    let mut rerun_enabled = false;
    #[cfg(feature = "rerun")]
    let mut rerun_save_path: Option<String> = None;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--run-seconds" => {
                if i + 1 < args.len() {
                    run_seconds = args[i + 1].parse::<u64>().ok();
                    i += 1;
                }
            }
            "--bind" => {
                if i + 1 < args.len() {
                    bind_addr = args[i + 1].clone();
                    i += 1;
                }
            }
            "--no-bridge" => {
                bridge_enabled = false;
            }
            "--json-logs" => {
                json_logs = true;
            }
            "--metrics-addr" => {
                if i + 1 < args.len() {
                    metrics_addr = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            "--audit-log" => {
                if i + 1 < args.len() {
                    audit_path = Some(PathBuf::from(&args[i + 1]));
                    i += 1;
                }
            }
            "--tls-cert" => {
                if i + 1 < args.len() {
                    tls_cert = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            "--tls-key" => {
                if i + 1 < args.len() {
                    tls_key = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            "--auth-secret" => {
                if i + 1 < args.len() {
                    auth_secret = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            "--auth-max-age" => {
                if i + 1 < args.len() {
                    auth_max_age_secs = args[i + 1].parse().unwrap_or(300);
                    i += 1;
                }
            }
            "--modbus" => {
                if i + 1 < args.len() {
                    modbus_addr = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            #[cfg(feature = "opcua")]
            "--opcua" => {
                opcua_enabled = true;
            }
            #[cfg(feature = "opcua")]
            "--opcua-endpoint" => {
                if i + 1 < args.len() {
                    opcua_endpoint = args[i + 1].clone();
                    i += 1;
                }
            }
            #[cfg(feature = "rerun")]
            "--rerun" => {
                rerun_enabled = true;
            }
            #[cfg(feature = "rerun")]
            "--rerun-save" => {
                if i + 1 < args.len() {
                    rerun_enabled = true;
                    rerun_save_path = Some(args[i + 1].clone());
                    i += 1;
                }
            }
            "--help" | "-h" => {
                print_help();
                return;
            }
            _ => {}
        }
        i += 1;
    }

    // Initialize tracing
    init_tracing(json_logs);

    // Initialize metrics
    init_metrics();

    // Start metrics server if enabled
    let metrics_enabled = metrics_addr.is_some();
    let _metrics_handle = metrics_addr.map(|addr| {
        info!(addr = %addr, "Starting metrics server");
        serve_metrics(addr)
    });

    // Initialize audit logger if enabled
    let audit_logger = audit_path
        .as_ref()
        .map(|path| match AuditLogger::new(path) {
            Ok(logger) => {
                info!(path = %path.display(), "Audit logging enabled");
                Arc::<AuditLogger>::new(logger)
            }
            Err(e) => {
                warn!(error = %e, path = %path.display(), "Failed to initialize audit logger");
                panic!("Audit logging requested but failed to initialize: {}", e);
            }
        });

    // Log startup
    if let Some(ref logger) = audit_logger {
        let timebase = TimeBase::new();
        let _ = logger.log_event(
            timebase.now_us(),
            timebase.unix_us(),
            AuditEventType::SystemStart,
            serde_json::json!({
                "version": env!("CARGO_PKG_VERSION"),
                "bridge_enabled": bridge_enabled,
                "metrics_enabled": metrics_enabled,
            }),
        );
    }

    let control_config = ControlConfig::default();
    let exchange = Arc::new(StateExchange::new(
        control_config.recommendation_timeout.as_micros() as u64,
    ));
    let timebase = TimeBase::new();

    let stop = Arc::new(AtomicBool::new(false));

    let exchange_iron = Arc::clone(&exchange);
    let stop_iron = Arc::clone(&stop);
    let timebase_iron = timebase;
    let control_config_iron = control_config.clone();

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

    let bridge_handle = if bridge_enabled {
        let exchange_bridge = Arc::clone(&exchange);
        let stop_bridge = Arc::clone(&stop);
        let timebase_bridge = timebase;
        let bridge_config = BridgeConfig {
            bind_addr: bind_addr.clone(),
            tls: TlsConfig {
                enabled: tls_cert.is_some() && tls_key.is_some(),
                cert_path: tls_cert.unwrap_or_default(),
                key_path: tls_key.unwrap_or_default(),
            },
            auth: AuthConfig {
                enabled: auth_secret.is_some(),
                secret: auth_secret.unwrap_or_default().into_bytes(),
                max_age_secs: auth_max_age_secs,
            },
            ..Default::default()
        };
        info!(addr = %bind_addr, "Starting bridge");
        Some(thread::spawn(move || {
            run_bridge(exchange_bridge, timebase_bridge, bridge_config, stop_bridge);
        }))
    } else {
        info!("Bridge disabled");
        None
    };

    #[cfg(feature = "opcua")]
    let opcua_handle = if opcua_enabled {
        let mut opcua_config = OpcuaConfig::default();
        opcua_config.endpoint = opcua_endpoint.clone();
        info!(endpoint = %opcua_endpoint, "Starting OPC UA server");
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
    let rerun_handle = if rerun_enabled {
        let mut rerun_config = RerunConfig::default();
        rerun_config.save_path = rerun_save_path.map(std::path::PathBuf::from);
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

    if let Some(seconds) = run_seconds {
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

fn print_help() {
    println!(
        r#"NeuroPLC - Safety-first agentic industrial controller

USAGE:
    neuro-plc [OPTIONS]

OPTIONS:
    --bind <ADDR>           Bridge TCP bind address [default: 127.0.0.1:7000]
    --no-bridge             Disable the TCP bridge (standalone simulation)
    --run-seconds <SECS>    Run for a fixed duration then exit
    --json-logs             Output logs in JSON format (for log aggregation)
    --metrics-addr <ADDR>   Enable Prometheus metrics server on address (e.g., 0.0.0.0:9090)
    --audit-log <PATH>      Enable audit logging to specified JSONL file
    --tls-cert <PATH>       Path to TLS certificate (PEM) for bridge security
    --tls-key <PATH>        Path to TLS private key (PEM)
    --auth-secret <STR>     Shared secret for HMAC token authentication
    --auth-max-age <SECS>   Maximum age for auth tokens in seconds [default: 300]
    --modbus <ADDR>         Connect to real hardware via Modbus TCP (e.g. 192.168.1.10:502)
    --opcua                 Enable OPC UA server (requires 'opcua' feature)
    --opcua-endpoint <URL>  OPC UA endpoint URL [default: opc.tcp://0.0.0.0:4840]
    --rerun                 Enable Rerun visualization (requires 'rerun' feature)
    --rerun-save <PATH>     Save Rerun recording to file
    -h, --help              Print this help message

ENVIRONMENT VARIABLES:
    RUST_LOG                Set log filter (e.g., RUST_LOG=debug,neuro_plc=trace)

EXAMPLES:
    # Basic run with metrics
    neuro-plc --metrics-addr 0.0.0.0:9090

    # Production run with all observability
    neuro-plc --json-logs --metrics-addr 0.0.0.0:9090 --audit-log /var/log/neuroplc/audit.jsonl

    # Short test run
    neuro-plc --run-seconds 10 --no-bridge
"#
    );
}
