use crate::safety::{SafetyLimits, SafetyViolation, Setpoint};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SafetyState {
    #[default]
    Normal,
    Degraded,
    Trip,
    Safe,
}

pub struct SafetySupervisor {
    state: SafetyState,
    last_safe_setpoint: f64,
    limits: SafetyLimits,
    timing_violation_count: u32,
}

impl SafetySupervisor {
    pub fn new(limits: SafetyLimits) -> Self {
        Self {
            state: SafetyState::Normal,
            last_safe_setpoint: 0.0,
            limits,
            timing_violation_count: 0,
        }
    }

    pub fn state(&self) -> SafetyState {
        self.state
    }

    pub fn apply_recommendation(
        &mut self,
        target_speed: Option<f64>,
        current_speed: f64,
        current_temp: f64,
    ) -> (f64, Option<SafetyViolation>) {
        if matches!(self.state, SafetyState::Trip | SafetyState::Safe) {
            self.state = SafetyState::Safe;
            self.last_safe_setpoint = 0.0;
            return (0.0, None);
        }

        let target_speed = match target_speed {
            Some(value) => value,
            None => {
                self.state = SafetyState::Degraded;
                return (self.last_safe_setpoint, None);
            }
        };

        let raw_setpoint = Setpoint::new(target_speed);
        let validated = raw_setpoint.validate(&self.limits, current_speed, current_temp);

        match validated {
            Ok(safe_setpoint) => {
                let speed = safe_setpoint.value();
                self.last_safe_setpoint = speed;
                self.state = SafetyState::Normal;
                self.timing_violation_count = 0;
                (speed, None)
            }
            Err(violation) => {
                self.state = SafetyState::Trip;
                self.last_safe_setpoint = 0.0;
                (0.0, Some(violation))
            }
        }
    }

    pub fn note_timing_jitter(
        &mut self,
        jitter_us: u64,
        max_jitter_us: u64,
        trip_after: u32,
    ) -> bool {
        if jitter_us <= max_jitter_us {
            self.timing_violation_count = 0;
            return false;
        }

        self.timing_violation_count = self.timing_violation_count.saturating_add(1);
        if self.timing_violation_count >= trip_after.max(1) {
            self.state = SafetyState::Trip;
            self.last_safe_setpoint = 0.0;
        } else {
            self.state = SafetyState::Degraded;
        }
        true
    }
}

impl SafetyState {
    pub const fn as_str(&self) -> &'static str {
        match self {
            SafetyState::Normal => "normal",
            SafetyState::Degraded => "degraded",
            SafetyState::Trip => "trip",
            SafetyState::Safe => "safe",
        }
    }

    pub const fn as_u8(&self) -> u8 {
        match self {
            SafetyState::Normal => 0,
            SafetyState::Degraded => 1,
            SafetyState::Trip => 2,
            SafetyState::Safe => 3,
        }
    }
}
