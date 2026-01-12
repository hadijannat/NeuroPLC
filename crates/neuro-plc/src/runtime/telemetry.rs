use neuro_io::metrics::{init_metrics, serve_metrics};
use std::thread;
use tracing::info;

pub fn init() {
    init_metrics();
}

pub fn start_metrics_server(addr: &Option<String>) -> Option<thread::JoinHandle<()>> {
    addr.as_ref().map(|addr| {
        info!(addr = %addr, "Starting metrics server");
        serve_metrics(addr.clone())
    })
}
