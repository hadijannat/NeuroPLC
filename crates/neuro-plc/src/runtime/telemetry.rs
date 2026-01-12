use core_spine::StateExchange;
use neuro_io::metrics::{
    init_metrics, serve_metrics, AGENT_CONFIDENCE, AGENT_TARGET_RPM, CYCLES_EXECUTED,
    CYCLE_JITTER_US, MOTOR_SPEED_RPM, MOTOR_TEMP_C, PRESSURE_BAR,
};
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;
use tracing::info;

pub fn init() {
    init_metrics();
}

pub fn start_metrics_server(addr: &Option<String>) -> Option<thread::JoinHandle<()>> {
    addr.as_ref().map(|addr| {
        info!(addr = %addr, "Starting metrics server");
        serve_metrics(addr.clone())
    })
}

pub fn start_metrics_updater(
    exchange: Arc<StateExchange>,
    stop: Arc<AtomicBool>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let mut last_cycle_count = 0u64;
        while !stop.load(std::sync::atomic::Ordering::Relaxed) {
            let snapshot = exchange.read_state();
            MOTOR_SPEED_RPM.set(snapshot.motor_speed_rpm);
            MOTOR_TEMP_C.set(snapshot.motor_temp_c);
            PRESSURE_BAR.set(snapshot.pressure_bar);
            CYCLE_JITTER_US.observe(snapshot.cycle_jitter_us as f64);
            if snapshot.cycle_count > last_cycle_count {
                let delta = snapshot.cycle_count - last_cycle_count;
                CYCLES_EXECUTED.inc_by(delta as u64);
                last_cycle_count = snapshot.cycle_count;
            }

            if let Some(rec) = exchange.get_recommendation(snapshot.timestamp_us) {
                if let Some(target) = rec.target_speed_rpm {
                    AGENT_TARGET_RPM.set(target);
                }
                AGENT_CONFIDENCE.set(rec.confidence as f64);
            }

            thread::sleep(Duration::from_millis(200));
        }
    })
}
