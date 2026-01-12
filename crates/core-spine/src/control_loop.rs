use crate::hal::MachineIO;
use crate::safety::SafetyLimits;
use crate::safety_supervisor::{SafetyState, SafetySupervisor};
use crate::sync::{ProcessSnapshot, StateExchange};
use crate::timebase::TimeBase;
use std::sync::{atomic::AtomicBool, Arc};
use std::time::{Duration, Instant};

#[derive(Clone, Debug)]
pub struct ControlConfig {
    pub cycle_time: Duration,
    pub safety_limits: SafetyLimits,
    pub recommendation_timeout: Duration,
    pub watchdog_timeout: Duration,
}

impl Default for ControlConfig {
    fn default() -> Self {
        Self {
            cycle_time: Duration::from_millis(1),
            safety_limits: SafetyLimits {
                max_speed_rpm: 3000.0,
                min_speed_rpm: 0.0,
                max_rate_of_change: 50.0,
                max_temp_c: 80.0,
            },
            recommendation_timeout: Duration::from_millis(500),
            watchdog_timeout: Duration::from_millis(100),
        }
    }
}

#[derive(Clone, Default, Debug)]
pub struct ExecutionStats {
    pub cycles_executed: u64,
    pub cycles_missed: u64,
    pub max_jitter_us: u64,
    pub safety_rejections: u64,
    pub agent_timeouts: u64,
    pub last_recommendation_age_us: u64,
    pub safety_state: SafetyState,
}

pub struct IronThread<IO: MachineIO> {
    io: IO,
    config: ControlConfig,
    exchange: Arc<StateExchange>,
    stats: ExecutionStats,
    safety: SafetySupervisor,
    timebase: TimeBase,
}

impl<IO: MachineIO> IronThread<IO> {
    pub fn new(
        io: IO,
        config: ControlConfig,
        exchange: Arc<StateExchange>,
        timebase: TimeBase,
    ) -> Self {
        let safety = SafetySupervisor::new(config.safety_limits);
        Self {
            io,
            config,
            exchange,
            stats: ExecutionStats::default(),
            safety,
            timebase,
        }
    }

    pub fn run(&mut self, stop: &AtomicBool) {
        let mut next_cycle = Instant::now();
        let cycle_dt_s = self.config.cycle_time.as_secs_f64();

        while !stop.load(std::sync::atomic::Ordering::Relaxed) {
            let now = Instant::now();
            if now < next_cycle {
                while Instant::now() < next_cycle {
                    std::hint::spin_loop();
                }
            } else {
                self.stats.cycles_missed += 1;
                let overrun = now.duration_since(next_cycle);
                if overrun > self.config.watchdog_timeout {
                    self.emergency_stop();
                    break;
                }
            }

            let cycle_start = Instant::now();
            let timestamp_us = self.timebase.now_us();

            // Advance simulation / I/O
            self.io.step(cycle_dt_s);

            // Read inputs
            let current_speed = self.io.read_speed();
            let current_temp = self.io.read_temperature();
            let current_pressure = self.io.read_pressure();

            // Read AI recommendation (stale => None)
            let recommendation = self.exchange.get_recommendation(timestamp_us);
            let target_speed = match recommendation {
                Some(rec) if rec.target_speed_rpm.is_some() => {
                    self.stats.last_recommendation_age_us =
                        timestamp_us.saturating_sub(rec.timestamp_us);
                    rec.target_speed_rpm
                }
                _ => {
                    self.stats.agent_timeouts += 1;
                    None
                }
            };

            let (output_speed, violation) =
                self.safety
                    .apply_recommendation(target_speed, current_speed, current_temp);
            if violation.is_some() {
                self.stats.safety_rejections += 1;
            }
            self.stats.safety_state = self.safety.state();

            // Write outputs
            self.io.write_speed(output_speed);

            // Publish state
            let cycle_duration = cycle_start.elapsed();
            let jitter_us = if cycle_duration > self.config.cycle_time {
                (cycle_duration - self.config.cycle_time).as_micros() as u64
            } else {
                0
            };
            self.stats.max_jitter_us = self.stats.max_jitter_us.max(jitter_us);
            self.stats.cycles_executed += 1;

            self.exchange.publish_state(ProcessSnapshot {
                timestamp_us,
                motor_speed_rpm: current_speed,
                motor_temp_c: current_temp,
                pressure_bar: current_pressure,
                cycle_jitter_us: jitter_us as u32,
            });

            next_cycle += self.config.cycle_time;
        }
    }

    fn emergency_stop(&mut self) {
        self.io.write_speed(0.0);
    }

    pub fn stats(&self) -> &ExecutionStats {
        &self.stats
    }
}
