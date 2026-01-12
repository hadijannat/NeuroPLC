use std::cell::UnsafeCell;
use std::sync::atomic::{AtomicUsize, Ordering};

#[derive(Debug, Clone, Copy, Default)]
pub struct ProcessSnapshot {
    pub timestamp_us: u64,
    pub cycle_count: u64,
    pub motor_speed_rpm: f64,
    pub motor_temp_c: f64,
    pub pressure_bar: f64,
    pub cycle_jitter_us: u32,
}

#[derive(Debug, Clone, Copy)]
pub struct AgentRecommendation {
    pub timestamp_us: u64,
    pub target_speed_rpm: Option<f64>,
    pub confidence: f32,
    pub reasoning_hash: [u8; 32],
}

impl Default for AgentRecommendation {
    fn default() -> Self {
        Self {
            timestamp_us: 0,
            target_speed_rpm: None,
            confidence: 0.0,
            reasoning_hash: [0u8; 32],
        }
    }
}

struct TripleBuffer<T: Copy + Default> {
    slots: [UnsafeCell<T>; 3],
    index: AtomicUsize,
}

unsafe impl<T: Copy + Default + Send> Send for TripleBuffer<T> {}
unsafe impl<T: Copy + Default + Sync> Sync for TripleBuffer<T> {}

impl<T: Copy + Default> TripleBuffer<T> {
    fn new() -> Self {
        let slots = std::array::from_fn(|_| UnsafeCell::new(T::default()));
        Self {
            slots,
            index: AtomicUsize::new(0),
        }
    }

    fn write(&self, value: T) {
        let current = self.index.load(Ordering::Relaxed);
        let next = (current + 1) % 3;
        unsafe {
            *self.slots[next].get() = value;
        }
        self.index.store(next, Ordering::Release);
    }

    fn read(&self) -> T {
        let idx = self.index.load(Ordering::Acquire);
        unsafe { *self.slots[idx].get() }
    }
}

pub struct StateExchange {
    process_state: TripleBuffer<ProcessSnapshot>,
    agent_recommendation: TripleBuffer<AgentRecommendation>,
    max_recommendation_age_us: u64,
}

impl StateExchange {
    pub fn new(max_age_us: u64) -> Self {
        Self {
            process_state: TripleBuffer::new(),
            agent_recommendation: TripleBuffer::new(),
            max_recommendation_age_us: max_age_us,
        }
    }

    /// Called by Iron Thread every cycle (non-blocking)
    pub fn publish_state(&self, state: ProcessSnapshot) {
        self.process_state.write(state);
    }

    /// Called by Iron Thread to get latest recommendation
    pub fn get_recommendation(&self, current_time_us: u64) -> Option<AgentRecommendation> {
        let rec = self.agent_recommendation.read();
        let age = current_time_us.saturating_sub(rec.timestamp_us);
        if rec.timestamp_us == 0 || age > self.max_recommendation_age_us {
            None
        } else {
            Some(rec)
        }
    }

    /// Called by Bridge Thread
    pub fn submit_recommendation(&self, rec: AgentRecommendation) {
        self.agent_recommendation.write(rec);
    }

    /// Called by Bridge Thread
    pub fn read_state(&self) -> ProcessSnapshot {
        self.process_state.read()
    }
}
