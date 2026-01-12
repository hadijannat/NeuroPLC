from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

from .schemas import Constraints, RecommendationCandidate, StateObservation

if TYPE_CHECKING:
    from digital_twin import BasyxAdapter
    from .memory.store import DecisionStore


@dataclass
class AgentContext:
    """Context for tool execution including sensor history and digital twin."""
    obs: StateObservation
    constraints: Constraints
    last_recommendation: Optional[RecommendationCandidate]
    # Extended fields for agentic LLM
    speed_history: list[float] = field(default_factory=list)
    temp_history: list[float] = field(default_factory=list)
    basyx_adapter: Optional["BasyxAdapter"] = None


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
        # Extended tools for agentic LLM
        {
            "type": "function",
            "function": {
                "name": "get_speed_trend",
                "description": "Analyze motor speed trend over recent history. Returns statistics (avg, min, max, slope) useful for predicting future speed.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window_size": {
                            "type": "integer",
                            "description": "Number of recent observations to analyze (default: 10)",
                            "default": 10,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_temp_trend",
                "description": "Analyze motor temperature trend over recent history. Returns statistics useful for thermal management decisions.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "window_size": {
                            "type": "integer",
                            "description": "Number of recent observations to analyze (default: 10)",
                            "default": 10,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_digital_twin",
                "description": "Query the BaSyx digital twin for equipment parameters. Available properties: MaxSpeedRPM, MinSpeedRPM, MaxTemperatureC, MaxRateChangeRPM, SafetyIntegrityLevel, ManufacturerName, SerialNumber.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "property_name": {
                            "type": "string",
                            "description": "Name of the property to query from digital twin",
                            "enum": [
                                "MaxSpeedRPM",
                                "MinSpeedRPM",
                                "MaxTemperatureC",
                                "MaxRateChangeRPM",
                                "SafetyIntegrityLevel",
                                "ManufacturerName",
                                "SerialNumber",
                            ],
                        },
                    },
                    "required": ["property_name"],
                },
            },
        },
        # Memory tools for learning from past decisions
        {
            "type": "function",
            "function": {
                "name": "query_decision_history",
                "description": "Query past decisions made by the agent. Useful for understanding patterns and learning from past recommendations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "metric": {
                            "type": "string",
                            "enum": ["speed", "temp", "all"],
                            "description": "Filter by metric type. 'all' returns all metrics.",
                            "default": "all",
                        },
                        "time_range_minutes": {
                            "type": "integer",
                            "description": "Look back this many minutes (default: 60)",
                            "default": 60,
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum decisions to return (default: 10)",
                            "default": 10,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_similar_scenarios",
                "description": "Find similar past scenarios to the current observation. Returns past decisions with similar sensor readings and their outcomes.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "k": {
                            "type": "integer",
                            "description": "Number of similar scenarios to find (default: 5)",
                            "default": 5,
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_decision_outcome",
                "description": "Get the outcome of a past decision. Shows whether it was accepted and what actually happened.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trace_id": {
                            "type": "string",
                            "description": "The trace ID of the decision to look up",
                        },
                    },
                    "required": ["trace_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "record_feedback",
                "description": "Record feedback about a decision outcome. Use this to note whether a decision worked well.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "trace_id": {
                            "type": "string",
                            "description": "The trace ID of the decision",
                        },
                        "success": {
                            "type": "boolean",
                            "description": "Whether the decision was successful",
                        },
                        "notes": {
                            "type": "string",
                            "description": "Optional notes about the outcome",
                        },
                    },
                    "required": ["trace_id", "success"],
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

    # Extended tools
    if name == "get_speed_trend":
        return _compute_trend(ctx.speed_history, args.get("window_size", 10), "speed_rpm")

    if name == "get_temp_trend":
        return _compute_trend(ctx.temp_history, args.get("window_size", 10), "temp_c")

    if name == "query_digital_twin":
        return _query_digital_twin(args.get("property_name", ""), ctx)

    # Memory tools
    if name == "query_decision_history":
        from .memory import query_decision_history as do_query

        metric = args.get("metric", "all")
        minutes = args.get("time_range_minutes", 60)
        limit = args.get("limit", 10)

        now_us = int(time.time() * 1_000_000)
        start_us = now_us - (minutes * 60 * 1_000_000)

        results = do_query(
            metric=metric,
            time_range_us=(start_us, now_us),
            limit=limit,
        )
        return {
            "count": len(results),
            "decisions": results,
        }

    if name == "get_similar_scenarios":
        from .memory import get_similar_scenarios as do_similar

        k = args.get("k", 5)
        results = do_similar(observation=ctx.obs, k=k)
        return {
            "count": len(results),
            "scenarios": results,
        }

    if name == "get_decision_outcome":
        from .memory import get_decision_outcome as do_outcome

        trace_id = args.get("trace_id", "")
        result = do_outcome(trace_id)
        if result is None:
            return {"error": f"Decision not found: {trace_id}"}
        return result

    if name == "record_feedback":
        from .memory.store import OutcomeFeedback, get_decision_store

        store = get_decision_store()
        if store is None:
            return {"error": "Memory system not available"}

        feedback = OutcomeFeedback(
            trace_id=args.get("trace_id", ""),
            spine_accepted=args.get("success", False),
            notes=args.get("notes"),
            outcome_timestamp_us=int(time.time() * 1_000_000),
        )
        updated = store.record_feedback(feedback)
        return {"success": updated}

    raise ValueError(f"Unknown tool: {name}")


def _compute_trend(history: list[float], window_size: int, metric_name: str) -> dict[str, Any]:
    """Compute trend statistics from a history buffer."""
    if not history:
        return {
            "error": f"No {metric_name} history available",
            "count": 0,
        }

    # Take the last N values
    window = history[-window_size:] if len(history) > window_size else history
    count = len(window)

    if count == 0:
        return {"error": "Empty window", "count": 0}

    result: dict[str, Any] = {
        "count": count,
        "latest": window[-1],
        "avg": statistics.mean(window),
        "min": min(window),
        "max": max(window),
    }

    if count > 1:
        result["std_dev"] = statistics.stdev(window)
        # Simple linear regression for slope
        x_mean = (count - 1) / 2
        y_mean = result["avg"]
        numerator = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(window))
        denominator = sum((i - x_mean) ** 2 for i in range(count))
        result["slope"] = numerator / denominator if denominator != 0 else 0.0
        result["trend"] = "rising" if result["slope"] > 0.1 else ("falling" if result["slope"] < -0.1 else "stable")
    else:
        result["std_dev"] = 0.0
        result["slope"] = 0.0
        result["trend"] = "unknown"

    return result


def _query_digital_twin(property_name: str, ctx: AgentContext) -> dict[str, Any]:
    """Query the BaSyx digital twin for a property.

    Attempts to read from BaSyx first (with caching), falls back to constraints.
    """
    # Property to submodel mapping
    PROPERTY_MAP = {
        "MaxSpeedRPM": ("safety", "MaxSpeedRPM"),
        "MinSpeedRPM": ("safety", "MinSpeedRPM"),
        "MaxTemperatureC": ("safety", "MaxTemperatureC"),
        "MaxRateChangeRPM": ("safety", "MaxRateChangeRPM"),
        "SafetyIntegrityLevel": ("functional_safety", "SafetyIntegrityLevel"),
        "ManufacturerName": ("nameplate", "ManufacturerName"),
        "SerialNumber": ("nameplate", "SerialNumber"),
    }

    if property_name not in PROPERTY_MAP:
        return {"error": f"Unknown property: {property_name}"}

    submodel_type, prop_id = PROPERTY_MAP[property_name]

    # Try BaSyx first if adapter is available
    if ctx.basyx_adapter is not None:
        try:
            # Import cache utilities
            from digital_twin.cache import (
                get_property_cache,
                make_cache_key,
                get_ttl_for_submodel,
            )

            # Get the appropriate submodel ID
            submodel_id = _get_submodel_id(ctx.basyx_adapter, submodel_type)
            cache_key = make_cache_key(submodel_id, prop_id)

            # Check cache first
            cache = get_property_cache()
            if cache is not None:
                cached_value = cache.get(cache_key)
                if cached_value is not None:
                    return {
                        "property": property_name,
                        "value": cached_value,
                        "source": "digital_twin_cached",
                        "submodel": submodel_type,
                    }

            # Query BaSyx
            status, value = ctx.basyx_adapter.get_property(submodel_id, prop_id)
            if status == 200 and value is not None:
                # Cache the result
                if cache is not None:
                    ttl = get_ttl_for_submodel(submodel_type)
                    cache.set(cache_key, value, ttl)

                return {
                    "property": property_name,
                    "value": value,
                    "source": "digital_twin",
                    "submodel": submodel_type,
                }

            # BaSyx returned error status, fall through to fallback
        except Exception:
            # Any error, fall through to fallback
            pass

    # Fallback to constraints/defaults
    return _fallback_value(property_name, ctx)


def _get_submodel_id(adapter: "BasyxAdapter", submodel_type: str) -> str:
    """Get the submodel ID for a given type from the adapter config."""
    submodel_ids = {
        "safety": adapter.config.safety_submodel_id,
        "nameplate": adapter.config.nameplate_submodel_id,
        "functional_safety": adapter.config.func_safety_submodel_id,
        "operational": adapter.config.operational_submodel_id,
        "ai": adapter.config.ai_submodel_id,
    }
    return submodel_ids.get(submodel_type, "")


def _fallback_value(property_name: str, ctx: AgentContext) -> dict[str, Any]:
    """Get fallback value from constraints when BaSyx is unavailable."""
    fallback_map = {
        "MaxSpeedRPM": ctx.constraints.max_speed_rpm,
        "MinSpeedRPM": ctx.constraints.min_speed_rpm,
        "MaxTemperatureC": ctx.constraints.max_temp_c,
        "MaxRateChangeRPM": ctx.constraints.max_rate_rpm,
        "SafetyIntegrityLevel": "SIL2",
        "ManufacturerName": "NeuroPLC Demo",
        "SerialNumber": "UNKNOWN",
    }
    if property_name in fallback_map:
        return {
            "property": property_name,
            "value": fallback_map[property_name],
            "source": "constraints_fallback",
        }
    return {"error": f"Unknown property: {property_name}"}


def tool_result_to_message(result: Any) -> str:
    return json.dumps(result, separators=(",", ":"), ensure_ascii=True)
