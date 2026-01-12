pub mod control_loop;
pub mod hal;
pub mod hal_sim;
pub mod safety;
pub mod sync;
pub mod timebase;

pub use control_loop::{ControlConfig, ExecutionStats, IronThread};
pub use hal::MachineIO;
pub use hal_sim::SimulatedMotor;
pub use safety::{SafetyLimits, SafetyViolation, Setpoint, Unvalidated, Validated};
pub use sync::{AgentRecommendation, ProcessSnapshot, StateExchange};
pub use timebase::TimeBase;
