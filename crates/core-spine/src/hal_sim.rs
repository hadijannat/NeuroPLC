use crate::hal::{CycleStats, MachineIO};

/// Simulated motor with thermal dynamics.
#[derive(Debug, Clone)]
pub struct SimulatedMotor {
    speed_rpm: f64,
    temperature_c: f64,
    pressure_bar: f64,

    inertia: f64,
    friction_coeff: f64,
    thermal_mass: f64,
    heat_generation: f64,
    cooling_rate: f64,
    ambient_temp: f64,

    target_speed: f64,
    stats: CycleStats,
}

impl SimulatedMotor {
    pub fn new() -> Self {
        Self {
            speed_rpm: 0.0,
            temperature_c: 25.0,
            pressure_bar: 1.0,
            inertia: 0.5,
            friction_coeff: 0.01,
            thermal_mass: 500.0,
            heat_generation: 0.001,
            cooling_rate: 10.0,
            ambient_temp: 25.0,
            target_speed: 0.0,
            stats: CycleStats::default(),
        }
    }

    fn update_stats(&mut self, dt_s: f64) {
        let cycle_us = (dt_s * 1_000_000.0) as u64;
        self.stats.last_cycle_us = cycle_us;
        self.stats.max_cycle_us = self.stats.max_cycle_us.max(cycle_us);
    }
}

impl Default for SimulatedMotor {
    fn default() -> Self {
        Self::new()
    }
}

impl MachineIO for SimulatedMotor {
    fn step(&mut self, dt_s: f64) {
        // Motor speed response.
        let speed_error = self.target_speed - self.speed_rpm;
        let time_constant = self.inertia / self.friction_coeff;
        self.speed_rpm += speed_error * (1.0 - (-dt_s / time_constant).exp());

        // Thermal dynamics.
        let speed_rad_s = self.speed_rpm * std::f64::consts::PI / 30.0;
        let heat_in = self.heat_generation * speed_rad_s * speed_rad_s;
        let heat_out = self.cooling_rate * (self.temperature_c - self.ambient_temp);
        let delta_temp = (heat_in - heat_out) * dt_s / self.thermal_mass;
        self.temperature_c += delta_temp;

        // Pressure model: proportional to speed squared.
        self.pressure_bar = 1.0 + 0.0001 * self.speed_rpm * self.speed_rpm;

        self.update_stats(dt_s);
    }

    fn read_speed(&self) -> f64 {
        self.speed_rpm
    }

    fn read_temperature(&self) -> f64 {
        self.temperature_c
    }

    fn read_pressure(&self) -> f64 {
        self.pressure_bar
    }

    fn write_speed(&mut self, rpm: f64) {
        self.target_speed = rpm.max(0.0);
    }

    fn cycle_stats(&self) -> CycleStats {
        self.stats.clone()
    }

    fn is_healthy(&self) -> bool {
        self.temperature_c.is_finite() && self.temperature_c < 120.0 && self.speed_rpm >= 0.0
    }
}
