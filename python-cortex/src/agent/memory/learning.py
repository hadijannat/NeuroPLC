"""Adaptive learning system for NeuroPLC agent.

Enables the agent to learn from decision outcomes by:
- Weighting similar scenarios by success rate
- Adjusting confidence based on historical performance
- Providing few-shot examples from successful decisions
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..schemas import StateObservation
    from .store import DecisionStore


@dataclass
class LearningStats:
    """Aggregated learning statistics for a condition bucket."""

    bucket_key: str  # e.g., "temp:50-70,speed:1000-2000"
    total_decisions: int
    successful_decisions: int  # spine_accepted = True
    success_rate: float
    avg_confidence: float
    last_updated_us: int


@dataclass
class FewShotExample:
    """A successful past decision formatted for prompt injection."""

    observation_summary: dict[str, Any]
    action: str
    target_speed_rpm: float
    confidence: float
    reasoning: str
    outcome_success: bool


@dataclass
class ConditionBucket:
    """Defines temperature and speed ranges for bucketing."""

    temp_min: float
    temp_max: float
    speed_min: float
    speed_max: float
    action: Optional[str] = None

    @property
    def key(self) -> str:
        action_str = f",action:{self.action}" if self.action else ""
        return f"temp:{self.temp_min:.0f}-{self.temp_max:.0f},speed:{self.speed_min:.0f}-{self.speed_max:.0f}{action_str}"


# Default bucket boundaries
TEMP_BUCKETS = [(0, 30), (30, 50), (50, 70), (70, 80), (80, 150)]
SPEED_BUCKETS = [(0, 500), (500, 1000), (1000, 2000), (2000, 2500), (2500, 3000)]


def _get_bucket_for_value(value: float, buckets: list[tuple[float, float]]) -> tuple[float, float]:
    """Find the bucket that contains a value."""
    for min_val, max_val in buckets:
        if min_val <= value < max_val:
            return (min_val, max_val)
    # Return last bucket if value exceeds all
    return buckets[-1]


class AdaptiveLearner:
    """Manages learning from decision outcomes.

    Provides:
    - Success-weighted similarity search
    - Confidence adjustment based on historical performance
    - Few-shot examples from successful decisions
    - Aggregated learning statistics by condition bucket
    """

    def __init__(
        self,
        store: Optional["DecisionStore"] = None,
        success_weight: float = 0.3,
        cache_ttl_s: float = 60.0,
    ):
        """Initialize the adaptive learner.

        Args:
            store: Decision store for querying history
            success_weight: Weight for success rate in similarity (0-1)
            cache_ttl_s: Cache TTL in seconds
        """
        self._store = store
        self.success_weight = float(
            os.environ.get("NEUROPLC_LEARNING_SUCCESS_WEIGHT", str(success_weight))
        )
        self.cache_ttl_s = float(
            os.environ.get("NEUROPLC_LEARNING_CACHE_TTL_S", str(cache_ttl_s))
        )

        # Stats cache
        self._stats_cache: dict[str, LearningStats] = {}
        self._cache_updated_at: float = 0.0

    def _get_store(self) -> Optional["DecisionStore"]:
        """Get the decision store, lazily initializing if needed."""
        if self._store is None:
            from .store import get_decision_store

            self._store = get_decision_store()
        return self._store

    def _is_cache_valid(self) -> bool:
        """Check if the stats cache is still valid."""
        return (time.time() - self._cache_updated_at) < self.cache_ttl_s

    def _invalidate_cache(self) -> None:
        """Invalidate the stats cache."""
        self._stats_cache.clear()
        self._cache_updated_at = 0.0

    def get_success_weighted_similar(
        self,
        observation: "StateObservation",
        k: int = 5,
        similarity_threshold: float = 0.8,
    ) -> list[dict[str, Any]]:
        """Find similar scenarios weighted by success rate.

        Combined score = (1 - success_weight) * similarity + success_weight * outcome_score

        Args:
            observation: Current state observation
            k: Number of results to return
            similarity_threshold: Minimum similarity score (0-1)

        Returns:
            List of similar decisions with combined scores
        """
        store = self._get_store()
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

        def get_outcome_score(decision: dict) -> float:
            """Get outcome score: 1.0 if accepted, 0.0 if rejected, 0.5 if unknown."""
            spine_accepted = decision.get("spine_accepted")
            if spine_accepted is None:
                return 0.5  # Neutral for unknown outcomes
            return 1.0 if spine_accepted else 0.0

        # Get recent decisions (more than k for filtering)
        decisions = store.query_decisions(limit=min(k * 20, 500))

        # Calculate combined scores
        scored: list[tuple[float, float, dict, dict]] = []
        for d in decisions:
            try:
                obs = json.loads(d["observation_json"])
                similarity = calculate_similarity(obs, observation)

                if similarity >= similarity_threshold:
                    outcome_score = get_outcome_score(d)
                    combined = (1 - self.success_weight) * similarity + self.success_weight * outcome_score
                    scored.append((combined, similarity, d, obs))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # Sort by combined score and take top k
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for combined, similarity, d, obs in scored[:k]:
            results.append(
                {
                    "trace_id": d["trace_id"],
                    "combined_score": round(combined, 4),
                    "similarity": round(similarity, 4),
                    "outcome_score": get_outcome_score(d),
                    "timestamp_us": d["timestamp_unix_us"],
                    "observation": {
                        "motor_speed_rpm": obs.get("motor_speed_rpm"),
                        "motor_temp_c": obs.get("motor_temp_c"),
                        "pressure_bar": obs.get("pressure_bar"),
                    },
                    "decision": {
                        "action": d["action"],
                        "target_speed_rpm": d["target_speed_rpm"],
                        "confidence": d["confidence"],
                        "reasoning": d["reasoning"],
                    },
                    "outcome": {
                        "approved": bool(d["approved"]),
                        "spine_accepted": d.get("spine_accepted"),
                    },
                }
            )

        return results

    def compute_adjusted_confidence(
        self,
        base_confidence: float,
        observation: "StateObservation",
        action: Optional[str] = None,
    ) -> float:
        """Adjust confidence based on historical success rate.

        Formula: adjusted = base * (0.5 + 0.5 * success_rate)

        Args:
            base_confidence: Original confidence from LLM
            observation: Current state observation
            action: Optional action type filter

        Returns:
            Adjusted confidence value
        """
        stats = self._get_bucket_stats(observation, action)

        if stats is None or stats.total_decisions == 0:
            # No historical data - use conservative multiplier
            return base_confidence * 0.8

        # Apply adjustment based on success rate
        multiplier = 0.5 + 0.5 * stats.success_rate
        return min(base_confidence * multiplier, 1.0)

    def _get_bucket_stats(
        self,
        observation: "StateObservation",
        action: Optional[str] = None,
    ) -> Optional[LearningStats]:
        """Get learning stats for the bucket containing this observation."""
        temp_bucket = _get_bucket_for_value(observation.motor_temp_c, TEMP_BUCKETS)
        speed_bucket = _get_bucket_for_value(observation.motor_speed_rpm, SPEED_BUCKETS)

        bucket = ConditionBucket(
            temp_min=temp_bucket[0],
            temp_max=temp_bucket[1],
            speed_min=speed_bucket[0],
            speed_max=speed_bucket[1],
            action=action,
        )

        return self._get_stats_for_bucket(bucket)

    def _get_stats_for_bucket(self, bucket: ConditionBucket) -> Optional[LearningStats]:
        """Get cached stats for a bucket, refreshing if needed."""
        if self._is_cache_valid() and bucket.key in self._stats_cache:
            return self._stats_cache[bucket.key]

        # Compute stats from database
        stats = self._compute_bucket_stats(bucket)
        if stats is not None:
            self._stats_cache[bucket.key] = stats
            self._cache_updated_at = time.time()

        return stats

    def _compute_bucket_stats(self, bucket: ConditionBucket) -> Optional[LearningStats]:
        """Compute learning stats for a bucket from database."""
        store = self._get_store()
        if store is None:
            return None

        # Query decisions matching this bucket
        decisions = store.query_decisions(limit=1000)

        matching = []
        for d in decisions:
            try:
                obs = json.loads(d["observation_json"])
                temp = obs.get("motor_temp_c", 0)
                speed = obs.get("motor_speed_rpm", 0)

                # Check if in bucket
                if not (bucket.temp_min <= temp < bucket.temp_max):
                    continue
                if not (bucket.speed_min <= speed < bucket.speed_max):
                    continue
                if bucket.action and d["action"] != bucket.action:
                    continue

                matching.append(d)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        if not matching:
            return None

        total = len(matching)
        successful = sum(1 for d in matching if d.get("spine_accepted") == 1)
        confidences = [d["confidence"] for d in matching if d.get("confidence") is not None]

        return LearningStats(
            bucket_key=bucket.key,
            total_decisions=total,
            successful_decisions=successful,
            success_rate=successful / total if total > 0 else 0.0,
            avg_confidence=sum(confidences) / len(confidences) if confidences else 0.0,
            last_updated_us=int(time.time() * 1_000_000),
        )

    def get_learning_stats(
        self,
        temp_range: Optional[str] = None,
        speed_range: Optional[str] = None,
        action_type: Optional[str] = None,
    ) -> list[LearningStats]:
        """Get aggregated success metrics by condition bucket.

        Args:
            temp_range: Filter by temperature: "low" (0-50), "medium" (50-70), "high" (70+)
            speed_range: Filter by speed: "low" (0-1000), "medium" (1000-2000), "high" (2000+)
            action_type: Filter by action type

        Returns:
            List of LearningStats for matching buckets
        """
        # Map range names to bucket boundaries
        temp_ranges = {
            "low": [(0, 30), (30, 50)],
            "medium": [(50, 70)],
            "high": [(70, 80), (80, 150)],
        }
        speed_ranges = {
            "low": [(0, 500), (500, 1000)],
            "medium": [(1000, 2000)],
            "high": [(2000, 2500), (2500, 3000)],
        }

        # Determine which buckets to query
        temp_buckets_to_query = temp_ranges.get(temp_range, TEMP_BUCKETS) if temp_range else TEMP_BUCKETS
        speed_buckets_to_query = speed_ranges.get(speed_range, SPEED_BUCKETS) if speed_range else SPEED_BUCKETS

        results = []
        for temp_min, temp_max in temp_buckets_to_query:
            for speed_min, speed_max in speed_buckets_to_query:
                bucket = ConditionBucket(
                    temp_min=temp_min,
                    temp_max=temp_max,
                    speed_min=speed_min,
                    speed_max=speed_max,
                    action=action_type,
                )
                stats = self._get_stats_for_bucket(bucket)
                if stats is not None and stats.total_decisions > 0:
                    results.append(stats)

        return results

    def get_few_shot_examples(
        self,
        observation: "StateObservation",
        n: int = 3,
        min_confidence: float = 0.7,
    ) -> list[FewShotExample]:
        """Get successful past decisions as few-shot examples.

        Args:
            observation: Current state observation
            n: Number of examples to return
            min_confidence: Minimum confidence threshold for examples

        Returns:
            List of FewShotExample objects
        """
        n = int(os.environ.get("NEUROPLC_LEARNING_FEW_SHOT_COUNT", str(n)))
        min_confidence = float(
            os.environ.get("NEUROPLC_LEARNING_MIN_CONFIDENCE", str(min_confidence))
        )

        # Get similar scenarios weighted by success
        similar = self.get_success_weighted_similar(
            observation, k=n * 3, similarity_threshold=0.7
        )

        examples = []
        for s in similar:
            # Only include successful, high-confidence decisions
            if (
                s["outcome"]["spine_accepted"] is True
                and s["decision"]["confidence"] >= min_confidence
            ):
                examples.append(
                    FewShotExample(
                        observation_summary=s["observation"],
                        action=s["decision"]["action"],
                        target_speed_rpm=s["decision"]["target_speed_rpm"],
                        confidence=s["decision"]["confidence"],
                        reasoning=s["decision"]["reasoning"] or "",
                        outcome_success=True,
                    )
                )
                if len(examples) >= n:
                    break

        return examples

    def record_outcome(
        self,
        trace_id: str,
        spine_accepted: bool,
        actual_speed_rpm: Optional[float] = None,
    ) -> bool:
        """Record outcome and trigger learning update.

        Args:
            trace_id: The trace ID of the decision
            spine_accepted: Whether spine accepted the recommendation
            actual_speed_rpm: Actual speed after application

        Returns:
            True if outcome was recorded
        """
        store = self._get_store()
        if store is None:
            return False

        from .store import OutcomeFeedback

        feedback = OutcomeFeedback(
            trace_id=trace_id,
            spine_accepted=spine_accepted,
            actual_speed_rpm=actual_speed_rpm,
            outcome_timestamp_us=int(time.time() * 1_000_000),
        )

        success = store.record_feedback(feedback)

        # Invalidate cache to pick up new data
        if success:
            self._invalidate_cache()

        return success

    def format_learning_context(self, observation: "StateObservation") -> str:
        """Format learning context for injection into LLM prompt.

        Args:
            observation: Current state observation

        Returns:
            Formatted string with learning context
        """
        stats_list = self.get_learning_stats()

        if not stats_list:
            return "No historical data available yet."

        # Get bucket for current observation
        current_bucket = self._get_bucket_stats(observation)

        lines = []
        if current_bucket and current_bucket.total_decisions > 0:
            lines.append(
                f"For current conditions (temp: {observation.motor_temp_c:.0f}C, "
                f"speed: {observation.motor_speed_rpm:.0f} RPM):"
            )
            lines.append(
                f"  - Historical success rate: {current_bucket.success_rate:.1%} "
                f"({current_bucket.successful_decisions}/{current_bucket.total_decisions} decisions)"
            )
            lines.append(f"  - Average confidence: {current_bucket.avg_confidence:.2f}")
        else:
            lines.append("No historical data for current conditions.")

        return "\n".join(lines)

    def format_few_shot_examples(self, examples: list[FewShotExample]) -> str:
        """Format few-shot examples for injection into LLM prompt.

        Args:
            examples: List of FewShotExample objects

        Returns:
            Formatted string with examples
        """
        if not examples:
            return "No similar successful examples available."

        lines = []
        for i, ex in enumerate(examples, 1):
            lines.append(f"Example {i}:")
            lines.append(
                f"  Observation: speed={ex.observation_summary.get('motor_speed_rpm', 0):.0f} RPM, "
                f"temp={ex.observation_summary.get('motor_temp_c', 0):.1f}C"
            )
            lines.append(f"  Action: {ex.action}, target={ex.target_speed_rpm:.0f} RPM")
            lines.append(f"  Confidence: {ex.confidence:.2f}")
            if ex.reasoning:
                lines.append(f"  Reasoning: {ex.reasoning}")
            lines.append(f"  Outcome: {'Accepted' if ex.outcome_success else 'Rejected'}")
            lines.append("")

        return "\n".join(lines)


# Global learner instance
_LEARNER: Optional[AdaptiveLearner] = None


def get_adaptive_learner() -> Optional[AdaptiveLearner]:
    """Get or create the global adaptive learner.

    Returns:
        AdaptiveLearner instance or None if learning is disabled
    """
    global _LEARNER

    learning_enabled = os.environ.get("NEUROPLC_LEARNING_ENABLED", "1") in ("1", "true", "yes")
    if not learning_enabled:
        return None

    if _LEARNER is None:
        _LEARNER = AdaptiveLearner()

    return _LEARNER


def reset_adaptive_learner() -> None:
    """Reset the global learner (for testing)."""
    global _LEARNER
    _LEARNER = None
