# Safety Evidence Index

This index maps safety claims to concrete verification artifacts.

## Safety Functions → Evidence

| Safety Function | Evidence |
| --- | --- |
| SF-01 Overspeed Protection | `crates/core-spine/src/safety.rs` tests, `crates/core-spine/src/safety_proptest.rs` |
| SF-02 Rate Limiting | `crates/core-spine/src/safety.rs` tests |
| SF-03 Temperature Interlock | `crates/core-spine/src/safety.rs` tests |
| SF-04 Non-Finite Rejection | `crates/core-spine/src/safety.rs` tests |
| SF-05 Watchdog | `crates/core-spine/src/control_loop.rs` watchdog logic |

## Runtime Evidence

| Claim | Evidence |
| --- | --- |
| Timing jitter monitored | `crates/core-spine/src/control_loop.rs` + `neuroplc_timing_violations_total` |
| Safety decisions auditable | `crates/neuro-plc/src/infra/audit.rs` |
| Protocol version/TTL enforced | `crates/neuro-io/src/bridge.rs` |

## Observability Evidence

| Metric | Source |
| --- | --- |
| `neuroplc_cycle_jitter_microseconds` | `crates/neuro-io/src/metrics.rs` |
| `neuroplc_safety_state` | `crates/neuro-io/src/metrics.rs` |
| `neuroplc_safety_rejections_total` | `crates/neuro-io/src/metrics.rs` |
