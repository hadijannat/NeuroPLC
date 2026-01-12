use std::marker::PhantomData;

#[derive(Debug, Clone, Copy)]
pub struct Unvalidated;

#[derive(Debug, Clone, Copy)]
pub struct Validated;

#[derive(Debug, Clone, Copy)]
pub struct Setpoint<State = Unvalidated> {
    value: f64,
    _state: PhantomData<State>,
}

#[derive(Debug, Clone, Copy)]
pub struct SafetyLimits {
    pub max_speed_rpm: f64,
    pub min_speed_rpm: f64,
    pub max_rate_of_change: f64,
    pub max_temp_c: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SafetyViolation {
    NonFiniteSetpoint {
        requested: f64,
    },
    NonFiniteSensor {
        current_speed: f64,
        current_temp: f64,
    },
    ExceedsMaxSpeed {
        requested: f64,
        limit: f64,
    },
    BelowMinSpeed {
        requested: f64,
        limit: f64,
    },
    RateOfChangeTooHigh {
        delta: f64,
        limit: f64,
    },
    TemperatureInterlock {
        current_temp: f64,
        limit: f64,
    },
}

impl Setpoint<Unvalidated> {
    pub fn new(value: f64) -> Self {
        Self {
            value,
            _state: PhantomData,
        }
    }

    pub fn validate(
        self,
        limits: &SafetyLimits,
        current_speed: f64,
        current_temp: f64,
    ) -> Result<Setpoint<Validated>, SafetyViolation> {
        if !self.value.is_finite() {
            return Err(SafetyViolation::NonFiniteSetpoint {
                requested: self.value,
            });
        }
        if !current_speed.is_finite() || !current_temp.is_finite() {
            return Err(SafetyViolation::NonFiniteSensor {
                current_speed,
                current_temp,
            });
        }

        if self.value > limits.max_speed_rpm {
            return Err(SafetyViolation::ExceedsMaxSpeed {
                requested: self.value,
                limit: limits.max_speed_rpm,
            });
        }
        if self.value < limits.min_speed_rpm {
            return Err(SafetyViolation::BelowMinSpeed {
                requested: self.value,
                limit: limits.min_speed_rpm,
            });
        }

        let delta = (self.value - current_speed).abs();
        if delta > limits.max_rate_of_change {
            return Err(SafetyViolation::RateOfChangeTooHigh {
                delta,
                limit: limits.max_rate_of_change,
            });
        }

        if current_temp > limits.max_temp_c {
            return Err(SafetyViolation::TemperatureInterlock {
                current_temp,
                limit: limits.max_temp_c,
            });
        }

        Ok(Setpoint {
            value: self.value,
            _state: PhantomData,
        })
    }
}

impl Setpoint<Validated> {
    pub fn value(&self) -> f64 {
        self.value
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn limits() -> SafetyLimits {
        SafetyLimits {
            max_speed_rpm: 3000.0,
            min_speed_rpm: 0.0,
            max_rate_of_change: 100.0,
            max_temp_c: 80.0,
        }
    }

    #[test]
    fn rejects_nan_setpoint() {
        let res = Setpoint::new(f64::NAN).validate(&limits(), 0.0, 25.0);
        assert!(matches!(
            res,
            Err(SafetyViolation::NonFiniteSetpoint { .. })
        ));
    }

    #[test]
    fn rejects_nonfinite_sensor() {
        let res = Setpoint::new(10.0).validate(&limits(), f64::INFINITY, 25.0);
        assert!(matches!(res, Err(SafetyViolation::NonFiniteSensor { .. })));
    }

    #[test]
    fn accepts_valid_setpoint() {
        let res = Setpoint::new(100.0).validate(&limits(), 50.0, 25.0);
        assert!(res.is_ok());
    }
}
