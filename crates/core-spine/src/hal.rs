#[derive(Clone, Default, Debug)]
pub struct CycleStats {
    pub last_cycle_us: u64,
    pub max_cycle_us: u64,
    pub missed_cycles: u64,
}

pub trait MachineIO: Send {
    fn step(&mut self, dt_s: f64);
    fn read_speed(&self) -> f64;
    fn read_temperature(&self) -> f64;
    fn read_pressure(&self) -> f64;
    fn write_speed(&mut self, rpm: f64);
    fn cycle_stats(&self) -> CycleStats;
    fn is_healthy(&self) -> bool;
}
