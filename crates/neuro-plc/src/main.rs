mod bridge;
mod protocol;
#[cfg(feature = "opcua")]
mod opcua_server;
#[cfg(feature = "rerun")]
mod rerun_viz;

use bridge::{run_bridge, BridgeConfig};
use core_spine::{ControlConfig, IronThread, SimulatedMotor, StateExchange, TimeBase};
use log::info;
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;
#[cfg(feature = "opcua")]
use opcua_server::{run_opcua, OpcuaConfig};
#[cfg(feature = "rerun")]
use rerun_viz::{run_rerun, RerunConfig};

fn main() {
    env_logger::init();

    let args: Vec<String> = std::env::args().collect();
    let mut run_seconds: Option<u64> = None;
    let mut bind_addr = "127.0.0.1:7000".to_string();
    let mut bridge_enabled = true;
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
            _ => {}
        }
        i += 1;
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

    let iron_handle = thread::spawn(move || {
        let io = SimulatedMotor::new();
        let mut iron = IronThread::new(io, control_config_iron, exchange_iron, timebase_iron);
        iron.run(&stop_iron);
        iron.stats().clone()
    });

    let bridge_handle = if bridge_enabled {
        let exchange_bridge = Arc::clone(&exchange);
        let stop_bridge = Arc::clone(&stop);
        let timebase_bridge = timebase;
        let mut bridge_config = BridgeConfig::default();
        bridge_config.bind_addr = bind_addr;
        Some(thread::spawn(move || {
            run_bridge(exchange_bridge, timebase_bridge, bridge_config, stop_bridge);
        }))
    } else {
        None
    };

    #[cfg(feature = "opcua")]
    let opcua_handle = if opcua_enabled {
        let mut opcua_config = OpcuaConfig::default();
        opcua_config.endpoint = opcua_endpoint;
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
        run_rerun(Arc::clone(&exchange), timebase, Arc::clone(&stop), rerun_config)
    } else {
        None
    };

    info!("NeuroPLC running. Connect python-cortex to send recommendations.");

    if let Some(seconds) = run_seconds {
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
        info!("Run complete: {:?}", stats);
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
