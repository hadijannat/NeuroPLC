"""Tests for adaptive learning system."""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

from agent.schemas import Constraints, RecommendationCandidate, StateObservation
from agent.memory import (
    DecisionStore,
    DecisionRecord,
    get_decision_store,
    reset_decision_store,
)
from agent.memory.learning import (
    AdaptiveLearner,
    LearningStats,
    FewShotExample,
    get_adaptive_learner,
    reset_adaptive_learner,
    TEMP_BUCKETS,
    SPEED_BUCKETS,
    _get_bucket_for_value,
)


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database path."""
    db_path = tmp_path / "test_learning.db"
    yield db_path
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def decision_store(temp_db):
    """Create a decision store with temporary database."""
    store = DecisionStore(db_path=temp_db, max_decisions=100)
    yield store
    store.close()


@pytest.fixture
def learner(decision_store):
    """Create an adaptive learner with test store."""
    return AdaptiveLearner(store=decision_store, success_weight=0.3, cache_ttl_s=60.0)


@pytest.fixture
def sample_observation():
    """Create a sample state observation."""
    return StateObservation(
        motor_speed_rpm=1500.0,
        motor_temp_c=55.0,
        pressure_bar=5.0,
        safety_state="normal",
        timestamp_us=1000000,
    )


@pytest.fixture
def sample_candidate():
    """Create a sample recommendation candidate."""
    return RecommendationCandidate(
        action="adjust_setpoint",
        target_speed_rpm=1600.0,
        confidence=0.9,
        reasoning="Test recommendation",
    )


@pytest.fixture
def sample_constraints():
    """Create sample safety constraints."""
    return Constraints(
        min_speed_rpm=0.0,
        max_speed_rpm=3000.0,
        max_rate_rpm=50.0,
        max_temp_c=80.0,
        staleness_us=500000,
    )


def create_test_decisions(store, count, base_temp=50.0, base_speed=1500.0, success_rate=0.8):
    """Helper to create test decisions with varying outcomes."""
    decisions = []
    for i in range(count):
        obs = StateObservation(
            motor_speed_rpm=base_speed + (i % 5) * 100,
            motor_temp_c=base_temp + (i % 3) * 5,
            pressure_bar=5.0,
            safety_state="normal",
            timestamp_us=1000000 + i * 1000,
        )
        candidate = RecommendationCandidate(
            action="adjust_setpoint" if i % 2 == 0 else "hold",
            target_speed_rpm=base_speed + (i % 5) * 50,
            confidence=0.8 + (i % 3) * 0.05,
            reasoning=f"Test decision {i}",
        )
        constraints = Constraints()

        record = DecisionRecord(
            trace_id=f"test-{i:04d}",
            timestamp_unix_us=int(time.time() * 1_000_000) + i * 1000,
            observation=obs,
            candidate=candidate,
            constraints=constraints,
            engine="test",
            approved=True,
        )
        store.record_decision(record)

        # Record outcome based on success_rate
        is_success = (i / count) < success_rate
        from agent.memory.store import OutcomeFeedback

        feedback = OutcomeFeedback(
            trace_id=f"test-{i:04d}",
            spine_accepted=is_success,
            actual_speed_rpm=candidate.target_speed_rpm if is_success else base_speed,
        )
        store.record_feedback(feedback)

        decisions.append(record)

    return decisions


class TestBucketFunctions:
    """Test bucket utility functions."""

    def test_get_bucket_for_temp(self):
        """Test temperature bucket assignment."""
        assert _get_bucket_for_value(25.0, TEMP_BUCKETS) == (0, 30)
        assert _get_bucket_for_value(35.0, TEMP_BUCKETS) == (30, 50)
        assert _get_bucket_for_value(60.0, TEMP_BUCKETS) == (50, 70)
        assert _get_bucket_for_value(75.0, TEMP_BUCKETS) == (70, 80)
        assert _get_bucket_for_value(100.0, TEMP_BUCKETS) == (80, 150)

    def test_get_bucket_for_speed(self):
        """Test speed bucket assignment."""
        assert _get_bucket_for_value(250.0, SPEED_BUCKETS) == (0, 500)
        assert _get_bucket_for_value(750.0, SPEED_BUCKETS) == (500, 1000)
        assert _get_bucket_for_value(1500.0, SPEED_BUCKETS) == (1000, 2000)
        assert _get_bucket_for_value(2250.0, SPEED_BUCKETS) == (2000, 2500)
        assert _get_bucket_for_value(2750.0, SPEED_BUCKETS) == (2500, 3000)


class TestAdaptiveLearner:
    """Tests for AdaptiveLearner class."""

    def test_create_learner(self, learner):
        """Test learner creation."""
        assert learner is not None
        assert learner.success_weight == 0.3
        assert learner.cache_ttl_s == 60.0

    def test_get_success_weighted_similar_empty(self, learner, sample_observation):
        """Test similarity search with empty database."""
        results = learner.get_success_weighted_similar(sample_observation, k=5)
        assert results == []

    def test_get_success_weighted_similar_with_data(
        self, decision_store, sample_observation
    ):
        """Test similarity search returns success-weighted results."""
        create_test_decisions(decision_store, 20, base_temp=55.0, base_speed=1500.0)

        learner = AdaptiveLearner(store=decision_store, success_weight=0.3)
        results = learner.get_success_weighted_similar(sample_observation, k=5)

        assert len(results) <= 5
        # Results should have combined_score
        for r in results:
            assert "combined_score" in r
            assert "similarity" in r
            assert "outcome_score" in r
            assert 0 <= r["combined_score"] <= 1

    def test_success_weighted_prefers_successful(self, decision_store, sample_observation):
        """Test that successful decisions are weighted higher."""
        # Create decisions - some successful, some not
        create_test_decisions(decision_store, 10, base_temp=55.0, success_rate=0.5)

        learner = AdaptiveLearner(store=decision_store, success_weight=0.5)
        results = learner.get_success_weighted_similar(sample_observation, k=10)

        # With 0.5 success weight, successful decisions should rank higher
        # (assuming similar base similarity)
        if len(results) >= 2:
            # Check that at least some successful ones are near the top
            top_3_outcomes = [r["outcome"]["spine_accepted"] for r in results[:3]]
            assert True in top_3_outcomes  # At least one successful in top 3

    def test_compute_adjusted_confidence_high_success(self, decision_store, sample_observation):
        """Test confidence adjustment with high success rate."""
        create_test_decisions(decision_store, 20, base_temp=55.0, success_rate=0.9)

        learner = AdaptiveLearner(store=decision_store)

        adjusted = learner.compute_adjusted_confidence(
            base_confidence=0.8,
            observation=sample_observation,
            action="adjust_setpoint",
        )

        # With 90% success rate, multiplier should be ~0.95
        # adjusted = 0.8 * (0.5 + 0.5 * 0.9) = 0.8 * 0.95 = 0.76
        assert 0.7 <= adjusted <= 0.85

    def test_compute_adjusted_confidence_low_success(self, decision_store, sample_observation):
        """Test confidence adjustment with low success rate."""
        create_test_decisions(decision_store, 20, base_temp=55.0, success_rate=0.3)

        learner = AdaptiveLearner(store=decision_store)

        adjusted = learner.compute_adjusted_confidence(
            base_confidence=0.8,
            observation=sample_observation,
            action="adjust_setpoint",
        )

        # With 30% success rate, multiplier should be ~0.65
        # adjusted = 0.8 * (0.5 + 0.5 * 0.3) = 0.8 * 0.65 = 0.52
        assert 0.4 <= adjusted <= 0.7

    def test_compute_adjusted_confidence_no_data(self, learner, sample_observation):
        """Test confidence adjustment with no historical data."""
        adjusted = learner.compute_adjusted_confidence(
            base_confidence=0.9,
            observation=sample_observation,
        )

        # No data should use conservative 0.8 multiplier
        assert adjusted == pytest.approx(0.72, abs=0.05)  # 0.9 * 0.8 = 0.72

    def test_get_few_shot_examples_empty(self, learner, sample_observation):
        """Test few-shot examples with empty database."""
        examples = learner.get_few_shot_examples(sample_observation, n=3)
        assert examples == []

    def test_get_few_shot_examples_filters_unsuccessful(
        self, decision_store, sample_observation
    ):
        """Test that few-shot examples only include successful decisions."""
        create_test_decisions(decision_store, 20, base_temp=55.0, success_rate=0.5)

        learner = AdaptiveLearner(store=decision_store)
        examples = learner.get_few_shot_examples(sample_observation, n=3, min_confidence=0.7)

        # All examples should be successful
        for ex in examples:
            assert ex.outcome_success is True
            assert ex.confidence >= 0.7

    def test_get_learning_stats_empty(self, learner):
        """Test learning stats with empty database."""
        stats = learner.get_learning_stats()
        assert stats == []

    def test_get_learning_stats_with_data(self, decision_store):
        """Test learning stats aggregation."""
        create_test_decisions(decision_store, 50, base_temp=55.0, base_speed=1500.0)

        learner = AdaptiveLearner(store=decision_store)
        stats = learner.get_learning_stats()

        # Should have stats for buckets that have data
        assert len(stats) > 0
        for s in stats:
            assert isinstance(s, LearningStats)
            assert s.total_decisions > 0
            assert 0 <= s.success_rate <= 1

    def test_get_learning_stats_with_filters(self, decision_store):
        """Test learning stats with temperature/speed filters."""
        create_test_decisions(decision_store, 50, base_temp=55.0, base_speed=1500.0)

        learner = AdaptiveLearner(store=decision_store)

        # Filter by medium temperature
        stats = learner.get_learning_stats(temp_range="medium")
        for s in stats:
            assert "temp:50-70" in s.bucket_key

    def test_record_outcome(self, decision_store, sample_observation, sample_candidate, sample_constraints):
        """Test recording outcome updates learning."""
        # Create a decision
        record = DecisionRecord(
            trace_id="outcome-test-001",
            timestamp_unix_us=int(time.time() * 1_000_000),
            observation=sample_observation,
            candidate=sample_candidate,
            constraints=sample_constraints,
            engine="test",
            approved=True,
        )
        decision_store.record_decision(record)

        learner = AdaptiveLearner(store=decision_store)

        # Record outcome
        success = learner.record_outcome(
            trace_id="outcome-test-001",
            spine_accepted=True,
            actual_speed_rpm=1595.0,
        )
        assert success is True

        # Verify outcome was recorded
        decision = decision_store.get_decision("outcome-test-001")
        assert decision["spine_accepted"] == 1
        assert decision["actual_speed_rpm"] == 1595.0

    def test_format_learning_context(self, decision_store, sample_observation):
        """Test formatting learning context for prompt."""
        create_test_decisions(decision_store, 20, base_temp=55.0, success_rate=0.8)

        learner = AdaptiveLearner(store=decision_store)
        context = learner.format_learning_context(sample_observation)

        assert isinstance(context, str)
        assert len(context) > 0
        # Should contain some stats information
        assert "success rate" in context.lower() or "historical" in context.lower()

    def test_format_few_shot_examples(self, decision_store, sample_observation):
        """Test formatting few-shot examples for prompt."""
        create_test_decisions(decision_store, 20, base_temp=55.0, success_rate=0.9)

        learner = AdaptiveLearner(store=decision_store)
        examples = learner.get_few_shot_examples(sample_observation, n=3)
        formatted = learner.format_few_shot_examples(examples)

        if examples:
            assert "Example" in formatted
            assert "Observation" in formatted
            assert "Action" in formatted
        else:
            assert "No similar" in formatted

    def test_cache_invalidation(self, decision_store, sample_observation):
        """Test that cache is invalidated on outcome recording."""
        create_test_decisions(decision_store, 10, base_temp=55.0, success_rate=0.8)

        learner = AdaptiveLearner(store=decision_store, cache_ttl_s=3600.0)

        # Get initial stats (populates cache)
        stats1 = learner.get_learning_stats()
        cache_time1 = learner._cache_updated_at

        # Record a new outcome
        learner.record_outcome(
            trace_id="test-0005",
            spine_accepted=False,
            actual_speed_rpm=1400.0,
        )

        # Cache should be invalidated
        assert learner._cache_updated_at == 0.0


class TestLearningTool:
    """Tests for get_learning_stats tool."""

    @pytest.fixture(autouse=True)
    def setup_global_store(self, temp_db):
        """Setup global store for tool tests."""
        reset_decision_store()
        reset_adaptive_learner()
        os.environ["NEUROPLC_DECISION_DB"] = str(temp_db)
        os.environ["NEUROPLC_LEARNING_ENABLED"] = "1"

        store = get_decision_store()
        create_test_decisions(store, 30, base_temp=55.0, success_rate=0.7)

        yield

        reset_decision_store()
        reset_adaptive_learner()

    def test_tool_definition_exists(self):
        """Test that get_learning_stats tool is defined."""
        from agent.tools import tool_definitions

        definitions = tool_definitions()
        tool_names = [d["function"]["name"] for d in definitions]

        assert "get_learning_stats" in tool_names

    def test_tool_execution(self, sample_observation, sample_constraints, sample_candidate):
        """Test executing get_learning_stats tool."""
        from agent.tools import execute_tool, AgentContext

        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=sample_candidate,
        )

        result = execute_tool("get_learning_stats", {}, ctx)

        assert "count" in result
        assert "stats" in result
        assert isinstance(result["stats"], list)

    def test_tool_with_filters(self, sample_observation, sample_constraints, sample_candidate):
        """Test get_learning_stats with filters."""
        from agent.tools import execute_tool, AgentContext

        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=sample_candidate,
        )

        result = execute_tool(
            "get_learning_stats",
            {"temp_range": "medium", "speed_range": "medium"},
            ctx,
        )

        assert result["filters"]["temp_range"] == "medium"
        assert result["filters"]["speed_range"] == "medium"


class TestGlobalSingletons:
    """Tests for global singleton management."""

    def test_get_adaptive_learner_disabled(self):
        """Test learner is None when disabled."""
        reset_adaptive_learner()
        os.environ["NEUROPLC_LEARNING_ENABLED"] = "0"

        learner = get_adaptive_learner()
        assert learner is None

        os.environ["NEUROPLC_LEARNING_ENABLED"] = "1"
        reset_adaptive_learner()

    def test_get_adaptive_learner_singleton(self, temp_db):
        """Test learner singleton behavior."""
        reset_adaptive_learner()
        reset_decision_store()
        os.environ["NEUROPLC_DECISION_DB"] = str(temp_db)
        os.environ["NEUROPLC_LEARNING_ENABLED"] = "1"

        learner1 = get_adaptive_learner()
        learner2 = get_adaptive_learner()

        assert learner1 is learner2

        reset_adaptive_learner()
        reset_decision_store()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
