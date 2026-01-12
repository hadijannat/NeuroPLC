#[derive(Debug, Clone, Copy)]
pub struct Tag {
    pub key: &'static str,
    pub metric: &'static str,
    pub opcua_node: &'static str,
    pub rerun_path: &'static str,
}

pub const MOTOR_SPEED_RPM: Tag = Tag {
    key: "motor_speed_rpm",
    metric: "neuroplc_motor_speed_rpm",
    opcua_node: "MotorSpeedRPM",
    rerun_path: "motor/speed/actual",
};

pub const MOTOR_TEMP_C: Tag = Tag {
    key: "motor_temp_c",
    metric: "neuroplc_motor_temperature_celsius",
    opcua_node: "MotorTemperatureC",
    rerun_path: "motor/temperature",
};

pub const PRESSURE_BAR: Tag = Tag {
    key: "pressure_bar",
    metric: "neuroplc_system_pressure_bar",
    opcua_node: "SystemPressureBar",
    rerun_path: "motor/pressure",
};

pub const CYCLE_JITTER_US: Tag = Tag {
    key: "cycle_jitter_us",
    metric: "neuroplc_cycle_jitter_microseconds",
    opcua_node: "CycleJitterUs",
    rerun_path: "system/cycle_jitter_us",
};

pub const TIMESTAMP_US: Tag = Tag {
    key: "timestamp_us",
    metric: "neuroplc_timestamp_us",
    opcua_node: "TimestampUs",
    rerun_path: "system/timestamp_us",
};

pub const AGENT_TARGET_RPM: Tag = Tag {
    key: "agent_target_rpm",
    metric: "neuroplc_agent_target_rpm",
    opcua_node: "AgentTargetRPM",
    rerun_path: "motor/speed/agent_target",
};

pub const AGENT_CONFIDENCE: Tag = Tag {
    key: "agent_confidence",
    metric: "neuroplc_agent_confidence",
    opcua_node: "AgentConfidence",
    rerun_path: "motor/agent/confidence",
};
