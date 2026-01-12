"""Query functions for agent memory system."""
from __future__ import annotations

import json
import math
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas import StateObservation

from .store import get_decision_store


def query_decision_history(
    metric: Optional[str] = None,
    time_range_us: Optional[tuple[int, int]] = None,
    limit: int = 50,
    engine: Optional[str] = None,
    approved_only: bool = False,
) -> list[dict[str, Any]]:
    """Query past decisions with filtering.

    Args:
        metric: Filter by metric type (e.g., "speed", "temp", "all")
        time_range_us: Tuple of (start_us, end_us) timestamps
        limit: Maximum results to return
        engine: Filter by inference engine
        approved_only: Only return approved decisions

    Returns:
        List of decision summaries
    """
    store = get_decision_store()
    if store is None:
        return []

    start_us = time_range_us[0] if time_range_us else None
    end_us = time_range_us[1] if time_range_us else None

    decisions = store.query_decisions(
        start_time_us=start_us,
        end_time_us=end_us,
        engine=engine,
        approved_only=approved_only,
        limit=limit,
    )

    results = []
    for d in decisions:
        try:
            obs = json.loads(d["observation_json"])
        except (json.JSONDecodeError, TypeError):
            obs = {}

        summary: dict[str, Any] = {
            "trace_id": d["trace_id"],
            "timestamp_us": d["timestamp_unix_us"],
            "action": d["action"],
            "target_speed_rpm": d["target_speed_rpm"],
            "confidence": d["confidence"],
            "reasoning": d["reasoning"],
            "approved": bool(d["approved"]),
            "engine": d["engine"],
            "model": d["model"],
        }

        # Include relevant metrics based on filter
        if metric is None or metric == "all":
            summary["motor_speed_rpm"] = obs.get("motor_speed_rpm")
            summary["motor_temp_c"] = obs.get("motor_temp_c")
            summary["pressure_bar"] = obs.get("pressure_bar")
        elif metric == "speed":
            summary["motor_speed_rpm"] = obs.get("motor_speed_rpm")
        elif metric == "temp":
            summary["motor_temp_c"] = obs.get("motor_temp_c")

        # Include outcome if available
        if d.get("spine_accepted") is not None:
            summary["spine_accepted"] = bool(d["spine_accepted"])
            summary["actual_speed_rpm"] = d.get("actual_speed_rpm")

        results.append(summary)

    return results


def get_similar_scenarios(
    observation: "StateObservation",
    k: int = 5,
    similarity_threshold: float = 0.8,
) -> list[dict[str, Any]]:
    """Find similar past scenarios to the current observation.

    Uses normalized Euclidean distance on speed, temperature, and pressure.

    Args:
        observation: Current state observation
        k: Number of similar scenarios to return
        similarity_threshold: Minimum similarity (0-1) to include

    Returns:
        List of similar past decisions with similarity scores
    """
    store = get_decision_store()
    if store is None:
        return []

    # Normalization ranges
    SPEED_RANGE = (0.0, 5000.0)
    TEMP_RANGE = (0.0, 150.0)
    PRESSURE_RANGE = (0.0, 20.0)

    def normalize(value: float, range_: tuple[float, float]) -> float:
        min_val, max_val = range_
        if max_val == min_val:
            return 0.0
        return (value - min_val) / (max_val - min_val)

    def calculate_similarity(obs1: dict, obs2: "StateObservation") -> float:
        """Calculate similarity between observations."""
        d_speed = normalize(obs1.get("motor_speed_rpm", 0), SPEED_RANGE) - normalize(
            obs2.motor_speed_rpm, SPEED_RANGE
        )
        d_temp = normalize(obs1.get("motor_temp_c", 0), TEMP_RANGE) - normalize(
            obs2.motor_temp_c, TEMP_RANGE
        )
        d_pressure = normalize(obs1.get("pressure_bar", 0), PRESSURE_RANGE) - normalize(
            obs2.pressure_bar, PRESSURE_RANGE
        )

        distance = math.sqrt(d_speed**2 + d_temp**2 + d_pressure**2)
        max_distance = math.sqrt(3)
        return 1.0 - (distance / max_distance)

    # Get recent decisions (more than k for filtering)
    decisions = store.query_decisions(limit=min(k * 10, 500))

    # Calculate similarities
    scored: list[tuple[float, dict, dict]] = []
    for d in decisions:
        try:
            obs = json.loads(d["observation_json"])
            similarity = calculate_similarity(obs, observation)
            if similarity >= similarity_threshold:
                scored.append((similarity, d, obs))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Sort by similarity and take top k
    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for similarity, d, obs in scored[:k]:
        results.append(
            {
                "trace_id": d["trace_id"],
                "similarity": round(similarity, 4),
                "timestamp_us": d["timestamp_unix_us"],
                "observation": {
                    "motor_speed_rpm": obs.get("motor_speed_rpm"),
                    "motor_temp_c": obs.get("motor_temp_c"),
                    "pressure_bar": obs.get("pressure_bar"),
                    "safety_state": obs.get("safety_state"),
                },
                "decision": {
                    "action": d["action"],
                    "target_speed_rpm": d["target_speed_rpm"],
                    "confidence": d["confidence"],
                    "reasoning": d["reasoning"],
                },
                "outcome": (
                    {
                        "approved": bool(d["approved"]),
                        "spine_accepted": d.get("spine_accepted"),
                        "actual_speed_rpm": d.get("actual_speed_rpm"),
                    }
                    if d.get("spine_accepted") is not None
                    else None
                ),
            }
        )

    return results


def get_decision_outcome(trace_id: str) -> Optional[dict[str, Any]]:
    """Get the outcome of a past decision.

    Args:
        trace_id: The trace ID of the decision

    Returns:
        Outcome dict or None if not found
    """
    store = get_decision_store()
    if store is None:
        return None

    decision = store.get_decision(trace_id)
    if decision is None:
        return None

    try:
        obs = json.loads(decision["observation_json"])
    except (json.JSONDecodeError, TypeError):
        obs = {}

    result: dict[str, Any] = {
        "trace_id": trace_id,
        "timestamp_us": decision["timestamp_unix_us"],
        "decision": {
            "action": decision["action"],
            "target_speed_rpm": decision["target_speed_rpm"],
            "confidence": decision["confidence"],
            "reasoning": decision["reasoning"],
        },
        "validation": {
            "approved": bool(decision["approved"]),
            "violations": json.loads(decision.get("violations_json", "[]")),
            "warnings": json.loads(decision.get("warnings_json", "[]")),
        },
        "context": {
            "motor_speed_rpm": obs.get("motor_speed_rpm"),
            "motor_temp_c": obs.get("motor_temp_c"),
            "engine": decision["engine"],
            "model": decision["model"],
        },
    }

    # Add outcome if available
    if decision.get("spine_accepted") is not None:
        result["outcome"] = {
            "spine_accepted": bool(decision["spine_accepted"]),
            "actual_speed_rpm": decision.get("actual_speed_rpm"),
            "outcome_timestamp_us": decision.get("outcome_timestamp_us"),
            "notes": decision.get("outcome_notes"),
        }
    else:
        result["outcome"] = None

    return result


def get_success_weighted_similar(
    observation: "StateObservation",
    k: int = 5,
    similarity_threshold: float = 0.8,
    success_weight: float = 0.3,
) -> list[dict[str, Any]]:
    """Find similar scenarios weighted by outcome success.

    Combined score = (1 - success_weight) * similarity + success_weight * outcome_score

    Args:
        observation: Current state observation
        k: Number of results to return
        similarity_threshold: Minimum similarity score (0-1)
        success_weight: Weight given to success rate (0-1)

    Returns:
        List of similar decisions with combined scores
    """
    from .learning import get_adaptive_learner

    learner = get_adaptive_learner()
    if learner is None:
        # Fallback to regular similarity search if learning disabled
        return get_similar_scenarios(observation, k, similarity_threshold)

    return learner.get_success_weighted_similar(observation, k, similarity_threshold)


def get_aggregated_stats(
    temp_min: Optional[float] = None,
    temp_max: Optional[float] = None,
    speed_min: Optional[float] = None,
    speed_max: Optional[float] = None,
    action: Optional[str] = None,
) -> dict[str, Any]:
    """Get aggregated success statistics for a condition range.

    Args:
        temp_min: Minimum temperature filter
        temp_max: Maximum temperature filter
        speed_min: Minimum speed filter
        speed_max: Maximum speed filter
        action: Action type filter

    Returns:
        Dict with aggregated statistics
    """
    store = get_decision_store()
    if store is None:
        return {"error": "Store not available", "total": 0}

    decisions = store.query_decisions(limit=1000)

    matching = []
    for d in decisions:
        try:
            obs = json.loads(d["observation_json"])
            temp = obs.get("motor_temp_c", 0)
            speed = obs.get("motor_speed_rpm", 0)

            # Apply filters
            if temp_min is not None and temp < temp_min:
                continue
            if temp_max is not None and temp >= temp_max:
                continue
            if speed_min is not None and speed < speed_min:
                continue
            if speed_max is not None and speed >= speed_max:
                continue
            if action is not None and d["action"] != action:
                continue

            matching.append(d)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    if not matching:
        return {
            "total": 0,
            "successful": 0,
            "success_rate": 0.0,
            "avg_confidence": 0.0,
            "filters": {
                "temp_range": (temp_min, temp_max),
                "speed_range": (speed_min, speed_max),
                "action": action,
            },
        }

    total = len(matching)
    successful = sum(1 for d in matching if d.get("spine_accepted") == 1)
    with_outcome = sum(1 for d in matching if d.get("spine_accepted") is not None)
    confidences = [d["confidence"] for d in matching if d.get("confidence") is not None]

    return {
        "total": total,
        "with_outcome": with_outcome,
        "successful": successful,
        "success_rate": successful / with_outcome if with_outcome > 0 else 0.0,
        "avg_confidence": sum(confidences) / len(confidences) if confidences else 0.0,
        "filters": {
            "temp_range": (temp_min, temp_max),
            "speed_range": (speed_min, speed_max),
            "action": action,
        },
    }
