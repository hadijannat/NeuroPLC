use core_spine::{CycleStats, MachineIO};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::runtime::Runtime;
use tokio::time::interval;
use tokio_modbus::prelude::*;
use tracing::{error, info, warn};

#[derive(Clone, Debug, Default)]
struct SharedState {
    speed_rpm: f64,
    temp_c: f64,
    pressure_bar: f64,
    target_speed_rpm: f64,
    connected: bool,
}

pub struct ModbusMotor {
    state: Arc<Mutex<SharedState>>,
    stats: CycleStats,
    last_cycle: Instant,
    _runtime: Arc<Runtime>, // Keep runtime alive
}

impl ModbusMotor {
    pub fn new(addr: &str) -> Option<Self> {
        let state = Arc::new(Mutex::new(SharedState::default()));
        let state_clone = state.clone();
        let addr = addr.to_string();

        let runtime = Arc::new(Runtime::new().expect("Failed to create Tokio runtime"));
        let runtime_clone = runtime.clone();

        // Spawn background polling task
        runtime.spawn(async move {
            let socket_addr = match addr.parse() {
                Ok(a) => a,
                Err(e) => {
                    error!("Invalid Modbus address {}: {}", addr, e);
                    return;
                }
            };

            let mut ctx = match tcp::connect(socket_addr).await {
                Ok(c) => {
                    info!("Connected to Modbus TCP at {}", addr);
                    c
                }
                Err(e) => {
                    error!("Failed to connect to Modbus TCP at {}: {}", addr, e);
                    return;
                }
            };

            let mut ticker = interval(Duration::from_millis(10)); // 100Hz polling

            loop {
                ticker.tick().await;

                // Read inputs
                // Example: Input Registers 0-3 (Speed, Temp, Pressure)
                match ctx.read_input_registers(0, 3).await {
                    Ok(data) => {
                        let mut state = state_clone.lock().unwrap();
                        state.connected = true;
                        if data.len() >= 3 {
                            // Scale values (example scaling)
                            state.speed_rpm = data[0] as f64;
                            state.temp_c = data[1] as f64 / 10.0;
                            state.pressure_bar = data[2] as f64 / 100.0;
                        }
                    }
                    Err(e) => {
                        warn!("Modbus read failed: {}", e);
                        let mut state = state_clone.lock().unwrap();
                        state.connected = false;
                        // Try reconnect logic here in real impl
                    }
                }

                // Write output
                let target = {
                    let state = state_clone.lock().unwrap();
                    state.target_speed_rpm
                };

                // Example: Holding Register 0
                let target_reg = target as u16;
                if let Err(e) = ctx.write_single_register(0, target_reg).await {
                    warn!("Modbus write failed: {}", e);
                }
            }
        });

        Some(Self {
            state,
            stats: CycleStats::default(),
            last_cycle: Instant::now(),
            _runtime: runtime_clone,
        })
    }
}

impl MachineIO for ModbusMotor {
    fn step(&mut self, _dt_s: f64) {
        let cycle_start = Instant::now();

        // In simulation/real HAL, stepping might involve physical simulation
        // or waiting for hardware ack. Here we just update stats as data
        // is exchanged in background.

        let cycle_us = cycle_start.elapsed().as_micros() as u64;
        self.stats.last_cycle_us = cycle_us;
        self.stats.max_cycle_us = self.stats.max_cycle_us.max(cycle_us);
        self.last_cycle = cycle_start;
    }

    fn read_speed(&self) -> f64 {
        self.state.lock().unwrap().speed_rpm
    }

    fn read_temperature(&self) -> f64 {
        self.state.lock().unwrap().temp_c
    }

    fn read_pressure(&self) -> f64 {
        self.state.lock().unwrap().pressure_bar
    }

    fn write_speed(&mut self, rpm: f64) {
        self.state.lock().unwrap().target_speed_rpm = rpm;
    }

    fn cycle_stats(&self) -> CycleStats {
        self.stats.clone()
    }

    fn is_healthy(&self) -> bool {
        self.state.lock().unwrap().connected
    }
}
