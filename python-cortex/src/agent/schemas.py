from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Constraints(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_speed_rpm: float = 0.0
    max_speed_rpm: float = 3000.0
    max_rate_rpm: float = 50.0
    max_temp_c: float = 80.0
    staleness_us: int = 500_000


class StateObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timestamp_us: int = 0
    unix_us: Optional[int] = None
    cycle_count: int = 0
    safety_state: str = "unknown"
    motor_speed_rpm: float = 0.0
    motor_temp_c: float = 25.0
    pressure_bar: float = 1.0
    cycle_jitter_us: int = 0


class RecommendationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["adjust_setpoint", "hold", "fallback", "review"] = "adjust_setpoint"
    target_speed_rpm: float = Field(..., description="Recommended speed in RPM")
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = ""


class Recommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["adjust_setpoint", "hold", "fallback", "review"] = "adjust_setpoint"
    target_speed_rpm: float = 0.0
    confidence: float = 0.0
    reasoning: str = ""
    approved: bool = False
    violations: list[str] = []
    warnings: list[str] = []
    trace_id: str = ""
