#![cfg(feature = "rerun")]

use core_spine::{StateExchange, TimeBase};
use log::{info, warn};
use rerun::{RecordingStream, RecordingStreamBuilder, Scalar};
use std::path::PathBuf;
use std::sync::{atomic::AtomicBool, Arc};
use std::thread;
use std::time::Duration;

#[derive(Clone, Debug)]
pub struct RerunConfig {
    pub update_interval: Duration,
    pub save_path: Option<PathBuf>,
}

impl Default for RerunConfig {
    fn default() -> Self {
        Self {
            update_interval: Duration::from_millis(100),
            save_path: None,
        }
    }
}

pub fn run_rerun(
    exchange: Arc<StateExchange>,
    timebase: TimeBase,
    stop: Arc<AtomicBool>,
    config: RerunConfig,
) -> Option<thread::JoinHandle<()>> {
    let rec = match config.save_path {
        Some(path) => RecordingStreamBuilder::new("NeuroPLC").save(path),
        None => RecordingStreamBuilder::new("NeuroPLC").spawn(),
    };

    let rec = match rec {
        Ok(r) => r,
        Err(err) => {
            warn!("Rerun init failed: {err}");
            return None;
        }
    };

    info!("Rerun viewer spawned");

    Some(thread::spawn(move || loop {
        if stop.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }

        log_snapshot(&rec, &exchange, &timebase);
        thread::sleep(config.update_interval);
    }))
}

fn log_snapshot(rec: &RecordingStream, exchange: &StateExchange, timebase: &TimeBase) {
    let snapshot = exchange.read_state();
    let time_s = snapshot.timestamp_us as f64 / 1_000_000.0;
    rec.set_time_seconds("sim_time", time_s);

    let _ = rec.log("motor/speed/actual", &Scalar::new(snapshot.motor_speed_rpm));
    let _ = rec.log("motor/temperature", &Scalar::new(snapshot.motor_temp_c));
    let _ = rec.log("motor/pressure", &Scalar::new(snapshot.pressure_bar));
    let _ = rec.log(
        "system/cycle_jitter_us",
        &Scalar::new(snapshot.cycle_jitter_us as f64),
    );

    if let Some(rec_msg) = exchange.get_recommendation(timebase.now_us()) {
        if let Some(target) = rec_msg.target_speed_rpm {
            let _ = rec.log("motor/speed/agent_target", &Scalar::new(target));
        }
        let _ = rec.log(
            "motor/agent/confidence",
            &Scalar::new(rec_msg.confidence as f64),
        );
    }
}
