from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional

from .schemas import Constraints, RecommendationCandidate, StateObservation


@dataclass
class AgentContext:
    obs: StateObservation
    constraints: Constraints
    last_recommendation: Optional[RecommendationCandidate]


def tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": "get_constraints",
                "description": "Return current safety constraints for recommendations.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_last_recommendation",
                "description": "Return the last recommendation candidate if available.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_state_summary",
                "description": "Return a concise summary of the latest state observation.",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compute_slew_limited_setpoint",
                "description": "Apply max rate limit to a target setpoint.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target_speed_rpm": {"type": "number"},
                        "current_speed_rpm": {"type": "number"},
                        "max_rate_rpm": {"type": "number"},
                    },
                    "required": ["target_speed_rpm"],
                },
            },
        },
    ]


def execute_tool(name: str, args: dict, ctx: AgentContext) -> Any:
    if name == "get_constraints":
        return ctx.constraints.model_dump()
    if name == "get_last_recommendation":
        if ctx.last_recommendation is None:
            return None
        return ctx.last_recommendation.model_dump()
    if name == "get_state_summary":
        obs = ctx.obs
        return {
            "motor_speed_rpm": obs.motor_speed_rpm,
            "motor_temp_c": obs.motor_temp_c,
            "pressure_bar": obs.pressure_bar,
            "safety_state": obs.safety_state,
            "cycle_jitter_us": obs.cycle_jitter_us,
            "timestamp_us": obs.timestamp_us,
        }
    if name == "compute_slew_limited_setpoint":
        target = float(args.get("target_speed_rpm", ctx.obs.motor_speed_rpm))
        current = float(args.get("current_speed_rpm", ctx.obs.motor_speed_rpm))
        max_rate = float(args.get("max_rate_rpm", ctx.constraints.max_rate_rpm))
        delta = target - current
        if abs(delta) > max_rate:
            return current + (max_rate if delta > 0 else -max_rate)
        return target
    raise ValueError(f"Unknown tool: {name}")


def tool_result_to_message(result: Any) -> str:
    return json.dumps(result, separators=(",", ":"), ensure_ascii=True)
