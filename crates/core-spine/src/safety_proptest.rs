#[cfg(test)]
mod proptest_safety {
    use crate::safety::*;
    use proptest::prelude::*;

    fn safety_limits() -> SafetyLimits {
        SafetyLimits {
            max_speed_rpm: 3000.0,
            min_speed_rpm: 0.0,
            max_rate_of_change: 100.0,
            max_temp_c: 80.0,
        }
    }

    proptest! {
        #![proptest_config(ProptestConfig::with_cases(10000))]

        // Property: Valid setpoints within bounds are always accepted
        #[test]
        fn valid_setpoints_accepted(
            current_speed in 0.0f64..=3000.0,
            delta in -100.0f64..=100.0, // Generate delta directly to ensure it respects rate limit
            current_temp in -40.0f64..=80.0,
        ) {
            let limits = safety_limits();
            // Calculate setpoint based on delta, clamped to valid range
            let setpoint = (current_speed + delta).max(limits.min_speed_rpm).min(limits.max_speed_rpm);

            // Re-verify delta due to clamping (clamping might reduce delta, which is fine,
            // but if clamping INCREASES delta it would be an issue - but clamping range only reduces delta magnitude)
            // Actually, clamping to 0..3000 when current is 0..3000 and delta is -100..100
            // is always safe regarding rate limit because |clamped - current| <= |(current+delta) - current| = |delta| <= 100

            let result = Setpoint::<Unvalidated>::new(setpoint)
                .validate(&limits, current_speed, current_temp);

            prop_assert!(result.is_ok(), "Failed for speed={}, delta={}, temp={}, result={:?}", current_speed, delta, current_temp, result);
        }

        // Property: Setpoints above max are always rejected
        #[test]
        fn overspeed_always_rejected(
            setpoint in 3000.01f64..10000.0,
            current_speed in 0.0f64..=3000.0,
            current_temp in -40.0f64..=80.0,
        ) {
            let limits = safety_limits();
            let result = Setpoint::<Unvalidated>::new(setpoint)
                .validate(&limits, current_speed, current_temp);

            let is_overspeed = matches!(result, Err(SafetyViolation::ExceedsMaxSpeed { .. }));
            prop_assert!(is_overspeed, "Expected ExceedsMaxSpeed, got {:?}", result);
        }

        // Property: Non-finite values are always rejected
        #[test]
        fn nonfinite_always_rejected(
            current_speed in 0.0f64..=3000.0,
            current_temp in -40.0f64..=80.0,
        ) {
            let limits = safety_limits();

            // Test NaN
            let nan_result = Setpoint::<Unvalidated>::new(f64::NAN)
                .validate(&limits, current_speed, current_temp);
            let is_nan_err = matches!(nan_result, Err(SafetyViolation::NonFiniteSetpoint { .. }));
            prop_assert!(is_nan_err, "Expected NonFiniteSetpoint for NaN, got {:?}", nan_result);

            // Test Infinity
            let inf_result = Setpoint::<Unvalidated>::new(f64::INFINITY)
                .validate(&limits, current_speed, current_temp);
            let is_inf_valid = matches!(
                inf_result,
                Err(SafetyViolation::NonFiniteSetpoint { .. }) |
                Err(SafetyViolation::ExceedsMaxSpeed { .. })
            );
            prop_assert!(is_inf_valid, "Expected NonFinite or ExceedsMax for Inf, got {:?}", inf_result);
        }

        // Property: Temperature interlock prevents all increases
        #[test]
        fn temp_interlock_blocks_operation(
            current_speed in 0.0f64..=3000.0,
            delta in 0.001f64..=100.0, // Ensure delta is positive and within rate limit
            current_temp in 80.01f64..=200.0,
        ) {
            let limits = safety_limits();
            // Try to increase speed
            let setpoint = (current_speed + delta).min(limits.max_speed_rpm);

            // Only test if we are actually trying to increase (clamping might make it equal)
            if setpoint > current_speed {
                let result = Setpoint::<Unvalidated>::new(setpoint)
                    .validate(&limits, current_speed, current_temp);

                 let is_interlock = matches!(result, Err(SafetyViolation::TemperatureInterlock { .. }));
                 prop_assert!(is_interlock, "Expected TemperatureInterlock, got {:?}", result);
            }
        }
    }
}
