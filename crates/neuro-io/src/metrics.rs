//! Prometheus metrics for NeuroPLC observability.
//!
//! This module provides metrics collection for the control loop,
//! safety system, and agent communication.

use core_spine::tags;
use prometheus::{Encoder, Gauge, Histogram, HistogramOpts, IntCounter, Registry, TextEncoder};
use std::sync::LazyLock;
use std::thread;
use tiny_http::{Response, Server};

/// Global metrics registry
pub static REGISTRY: LazyLock<Registry> = LazyLock::new(Registry::new);

// ============================================================================
// Control Loop Metrics
// ============================================================================

/// Total control loop cycles executed
pub static CYCLES_EXECUTED: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_cycles_executed_total",
        "Total control loop cycles executed",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Control loop cycles missed (overruns)
pub static CYCLES_MISSED: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_cycles_missed_total",
        "Control loop cycles missed due to timing overruns",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Control loop jitter distribution in microseconds
pub static CYCLE_JITTER_US: LazyLock<Histogram> = LazyLock::new(|| {
    let histogram = Histogram::with_opts(
        HistogramOpts::new(
            tags::CYCLE_JITTER_US.metric,
            "Control loop jitter distribution in microseconds",
        )
        .buckets(vec![
            1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0,
        ]),
    )
    .unwrap();
    REGISTRY.register(Box::new(histogram.clone())).unwrap();
    histogram
});

// ============================================================================
// Safety Metrics
// ============================================================================

/// Recommendations rejected by safety firewall
pub static SAFETY_REJECTIONS: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_safety_rejections_total",
        "Recommendations rejected by safety firewall",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Agent recommendation timeouts
pub static AGENT_TIMEOUTS: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_agent_timeouts_total",
        "Agent recommendation timeouts (stale or missing)",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Control loop timing violations (jitter over configured threshold)
pub static TIMING_VIOLATIONS: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_timing_violations_total",
        "Control loop timing violations (jitter threshold exceeded)",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Recommendation expired before processing
pub static RECOMMENDATION_EXPIRED: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_recommendation_expired_total",
        "Recommendations rejected due to expired TTL",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Recommendation sequence out-of-order
pub static RECOMMENDATION_OUT_OF_ORDER: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_recommendation_out_of_order_total",
        "Recommendations rejected due to out-of-order sequence",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Authentication failures for agent recommendations
pub static AUTH_FAILURES: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_auth_failures_total",
        "Recommendations rejected due to invalid auth tokens",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

/// Missing authentication tokens when required
pub static AUTH_MISSING: LazyLock<IntCounter> = LazyLock::new(|| {
    let counter = IntCounter::new(
        "neuroplc_auth_missing_total",
        "Recommendations rejected due to missing auth tokens",
    )
    .unwrap();
    REGISTRY.register(Box::new(counter.clone())).unwrap();
    counter
});

// ============================================================================
// Process State Metrics
// ============================================================================

/// Current motor speed in RPM
pub static MOTOR_SPEED_RPM: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(tags::MOTOR_SPEED_RPM.metric, "Current motor speed in RPM").unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

/// Current motor temperature in Celsius
pub static MOTOR_TEMP_C: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(
        tags::MOTOR_TEMP_C.metric,
        "Current motor temperature in Celsius",
    )
    .unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

/// Current system pressure in bar
pub static PRESSURE_BAR: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(tags::PRESSURE_BAR.metric, "Current system pressure in bar").unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

// ============================================================================
// Agent Metrics
// ============================================================================

/// Latest agent recommendation confidence score
pub static AGENT_CONFIDENCE: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(
        tags::AGENT_CONFIDENCE.metric,
        "Latest agent recommendation confidence score (0.0-1.0)",
    )
    .unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

/// Agent recommended target speed
pub static AGENT_TARGET_RPM: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(
        tags::AGENT_TARGET_RPM.metric,
        "Agent recommended target speed in RPM",
    )
    .unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

/// Bridge client connection status (1 = connected, 0 = disconnected)
pub static BRIDGE_CONNECTED: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(
        "neuroplc_bridge_connected",
        "Bridge client connection status (1=connected, 0=disconnected)",
    )
    .unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

/// Safety state (0=normal,1=degraded,2=trip,3=safe)
pub static SAFETY_STATE: LazyLock<Gauge> = LazyLock::new(|| {
    let gauge = Gauge::new(
        tags::SAFETY_STATE.metric,
        "Safety state (0=normal,1=degraded,2=trip,3=safe)",
    )
    .unwrap();
    REGISTRY.register(Box::new(gauge.clone())).unwrap();
    gauge
});

// ============================================================================
// Metrics HTTP Server
// ============================================================================

/// Start the metrics HTTP server on the given address.
/// Returns a join handle for the server thread.
pub fn serve_metrics(bind_addr: String) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        let server = match Server::http(&bind_addr) {
            Ok(s) => s,
            Err(e) => {
                tracing::error!("Failed to start metrics server on {}: {}", bind_addr, e);
                return;
            }
        };

        tracing::info!("Metrics server listening on http://{}/metrics", bind_addr);

        for request in server.incoming_requests() {
            let path = request.url();

            match path {
                "/metrics" => {
                    let encoder = TextEncoder::new();
                    let metric_families = REGISTRY.gather();
                    let mut buffer = Vec::new();

                    if let Err(e) = encoder.encode(&metric_families, &mut buffer) {
                        tracing::warn!("Failed to encode metrics: {}", e);
                        let _ = request.respond(
                            Response::from_string("Internal Server Error").with_status_code(500),
                        );
                        continue;
                    }

                    let response = Response::from_data(buffer).with_header(
                        tiny_http::Header::from_bytes(
                            &b"Content-Type"[..],
                            &b"text/plain; version=0.0.4"[..],
                        )
                        .unwrap(),
                    );
                    let _ = request.respond(response);
                }
                "/health" => {
                    let _ = request.respond(Response::from_string("OK"));
                }
                "/ready" => {
                    // Ready when we've executed at least one cycle
                    let cycles = CYCLES_EXECUTED.get();
                    if cycles > 0 {
                        let _ = request.respond(Response::from_string("Ready"));
                    } else {
                        let _ = request
                            .respond(Response::from_string("Not Ready").with_status_code(503));
                    }
                }
                _ => {
                    let _ =
                        request.respond(Response::from_string("Not Found").with_status_code(404));
                }
            }
        }
    })
}

/// Initialize all metrics (forces lazy initialization)
pub fn init_metrics() {
    // Touch each metric to force initialization
    let _ = CYCLES_EXECUTED.get();
    let _ = CYCLES_MISSED.get();
    let _ = CYCLE_JITTER_US.get_sample_count();
    let _ = SAFETY_REJECTIONS.get();
    let _ = AGENT_TIMEOUTS.get();
    let _ = TIMING_VIOLATIONS.get();
    let _ = RECOMMENDATION_EXPIRED.get();
    let _ = RECOMMENDATION_OUT_OF_ORDER.get();
    let _ = AUTH_FAILURES.get();
    let _ = AUTH_MISSING.get();
    let _ = MOTOR_SPEED_RPM.get();
    let _ = MOTOR_TEMP_C.get();
    let _ = PRESSURE_BAR.get();
    let _ = AGENT_CONFIDENCE.get();
    let _ = AGENT_TARGET_RPM.get();
    let _ = BRIDGE_CONNECTED.get();
    let _ = SAFETY_STATE.get();
}
