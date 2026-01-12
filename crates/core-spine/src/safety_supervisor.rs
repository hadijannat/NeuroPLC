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
}

impl SafetySupervisor {
    pub fn new(limits: SafetyLimits) -> Self {
        Self {
            state: SafetyState::Normal,
            last_safe_setpoint: 0.0,
            limits,
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
                (speed, None)
            }
            Err(violation) => {
                self.state = SafetyState::Trip;
                self.last_safe_setpoint = 0.0;
                (0.0, Some(violation))
            }
        }
    }
}
