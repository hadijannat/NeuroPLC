use std::time::{Instant, SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy)]
pub struct TimeBase {
    start: Instant,
}

impl TimeBase {
    pub fn new() -> Self {
        Self {
            start: Instant::now(),
        }
    }

    /// Monotonic microseconds since start.
    pub fn now_us(&self) -> u64 {
        self.start.elapsed().as_micros() as u64
    }

    /// Wall-clock microseconds since Unix epoch (for cross-process logs only).
    pub fn unix_us(&self) -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_micros() as u64
    }
}

impl Default for TimeBase {
    fn default() -> Self {
        Self::new()
    }
}
