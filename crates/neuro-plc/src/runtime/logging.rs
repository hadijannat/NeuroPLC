use tracing_subscriber::{fmt, prelude::*, EnvFilter};

/// Initialize the tracing subscriber with optional JSON output.
pub fn init_tracing(json_output: bool) {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,neuro_plc=debug,core_spine=debug"));

    if json_output {
        tracing_subscriber::registry()
            .with(filter)
            .with(fmt::layer().json())
            .init();
    } else {
        tracing_subscriber::registry()
            .with(filter)
            .with(fmt::layer().pretty())
            .init();
    }
}
