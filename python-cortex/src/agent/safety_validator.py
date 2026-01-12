from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import Constraints, Recommendation, RecommendationCandidate, StateObservation


@dataclass
class ValidationResult:
    approved: bool
    violations: list[str]
    warnings: list[str]
    target_speed_rpm: float


def validate_recommendation(
    candidate: RecommendationCandidate,
    obs: StateObservation,
    constraints: Constraints,
) -> ValidationResult:
    violations: list[str] = []
    warnings: list[str] = []

    target = float(candidate.target_speed_rpm)

    if not math.isfinite(target):
        violations.append("target_speed_rpm is not finite")
        target = 0.0

    if not math.isfinite(obs.motor_speed_rpm) or not math.isfinite(obs.motor_temp_c):
        violations.append("sensor values are not finite")

    if target < constraints.min_speed_rpm or target > constraints.max_speed_rpm:
        violations.append(
            f"target_speed_rpm {target} out of bounds "
            f"[{constraints.min_speed_rpm}, {constraints.max_speed_rpm}]"
        )
        target = min(max(target, constraints.min_speed_rpm), constraints.max_speed_rpm)

    delta = target - obs.motor_speed_rpm
    if abs(delta) > constraints.max_rate_rpm:
        warnings.append(
            f"rate_limit applied ({abs(delta):.2f} > {constraints.max_rate_rpm})"
        )
        target = obs.motor_speed_rpm + (
            constraints.max_rate_rpm if delta > 0 else -constraints.max_rate_rpm
        )

    if obs.motor_temp_c > constraints.max_temp_c:
        violations.append(
            f"temperature interlock {obs.motor_temp_c} > {constraints.max_temp_c}"
        )

    approved = len(violations) == 0
    return ValidationResult(
        approved=approved,
        violations=violations,
        warnings=warnings,
        target_speed_rpm=target,
    )


def materialize_recommendation(
    candidate: RecommendationCandidate,
    obs: StateObservation,
    constraints: Constraints,
    trace_id: str,
) -> Recommendation:
    validation = validate_recommendation(candidate, obs, constraints)
    return Recommendation(
        action=candidate.action,
        target_speed_rpm=validation.target_speed_rpm,
        confidence=float(candidate.confidence),
        reasoning=candidate.reasoning,
        approved=validation.approved,
        violations=validation.violations,
        warnings=validation.warnings,
        trace_id=trace_id,
    )
