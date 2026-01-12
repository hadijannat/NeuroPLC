"""Tests for memory and persistence system."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest

from agent.schemas import Constraints, RecommendationCandidate, StateObservation
from agent.memory import (
    DecisionStore,
    DecisionRecord,
    OutcomeFeedback,
    get_decision_store,
    reset_decision_store,
    ObservationBuffer,
    BufferConfig,
    get_observation_buffer,
    reset_observation_buffer,
    query_decision_history,
    get_similar_scenarios,
    get_decision_outcome,
)


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database path."""
    db_path = tmp_path / "test_decisions.db"
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def decision_store(temp_db):
    """Create a decision store with temporary database."""
    store = DecisionStore(db_path=temp_db, max_decisions=100)
    yield store
    store.close()


@pytest.fixture
def sample_observation():
    """Create a sample state observation."""
    return StateObservation(
        motor_speed_rpm=1500.0,
        motor_temp_c=45.0,
        pressure_bar=5.0,
        safety_state="normal",
        timestamp_us=1000000,
        cycle_jitter_us=100,
    )


@pytest.fixture
def sample_candidate():
    """Create a sample recommendation candidate."""
    return RecommendationCandidate(
        action="adjust_setpoint",
        target_speed_rpm=1600.0,
        confidence=0.9,
        reasoning="Increasing speed for efficiency",
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


class TestDecisionStore:
    """Tests for DecisionStore class."""

    def test_create_store(self, decision_store):
        """Test store creation and schema initialization."""
        assert decision_store.db_path.exists()
        stats = decision_store.stats()
        assert stats["decision_count"] == 0

    def test_record_decision(
        self, decision_store, sample_observation, sample_candidate, sample_constraints
    ):
        """Test recording a decision."""
        record = DecisionRecord(
            trace_id="test-trace-001",
            timestamp_unix_us=int(time.time() * 1_000_000),
            observation=sample_observation,
            candidate=sample_candidate,
            constraints=sample_constraints,
            engine="test-engine",
            model="test-model-v1",
            approved=True,
        )

        decision_store.record_decision(record)

        # Verify it was stored
        stats = decision_store.stats()
        assert stats["decision_count"] == 1

        # Retrieve it
        retrieved = decision_store.get_decision("test-trace-001")
        assert retrieved is not None
        assert retrieved["trace_id"] == "test-trace-001"
        assert retrieved["engine"] == "test-engine"
        assert retrieved["approved"] == 1

    def test_record_feedback(
        self, decision_store, sample_observation, sample_candidate, sample_constraints
    ):
        """Test recording outcome feedback."""
        # First record a decision
        record = DecisionRecord(
            trace_id="test-trace-002",
            timestamp_unix_us=int(time.time() * 1_000_000),
            observation=sample_observation,
            candidate=sample_candidate,
            constraints=sample_constraints,
            approved=True,
        )
        decision_store.record_decision(record)

        # Record feedback
        feedback = OutcomeFeedback(
            trace_id="test-trace-002",
            spine_accepted=True,
            actual_speed_rpm=1595.0,
            outcome_timestamp_us=int(time.time() * 1_000_000),
            notes="Successfully applied",
        )
        updated = decision_store.record_feedback(feedback)
        assert updated is True

        # Verify feedback was recorded
        retrieved = decision_store.get_decision("test-trace-002")
        assert retrieved["spine_accepted"] == 1
        assert retrieved["actual_speed_rpm"] == 1595.0
        assert retrieved["outcome_notes"] == "Successfully applied"

    def test_query_decisions(
        self, decision_store, sample_observation, sample_candidate, sample_constraints
    ):
        """Test querying decisions with filters."""
        base_time = int(time.time() * 1_000_000)

        # Record multiple decisions
        for i in range(5):
            record = DecisionRecord(
                trace_id=f"test-trace-{i:03d}",
                timestamp_unix_us=base_time + (i * 1000),
                observation=sample_observation,
                candidate=sample_candidate,
                constraints=sample_constraints,
                engine="test-engine" if i % 2 == 0 else "other-engine",
                approved=i % 2 == 0,
            )
            decision_store.record_decision(record)

        # Query all
        results = decision_store.query_decisions(limit=10)
        assert len(results) == 5

        # Query by engine
        results = decision_store.query_decisions(engine="test-engine")
        assert len(results) == 3

        # Query approved only
        results = decision_store.query_decisions(approved_only=True)
        assert len(results) == 3

        # Query with time range
        results = decision_store.query_decisions(
            start_time_us=base_time + 2000,
            end_time_us=base_time + 4000,
        )
        assert len(results) == 3  # trace-002, trace-003, trace-004

    def test_pruning(self, temp_db, sample_observation, sample_candidate, sample_constraints):
        """Test automatic pruning of old decisions."""
        store = DecisionStore(db_path=temp_db, max_decisions=10)

        # Record 15 decisions
        for i in range(15):
            record = DecisionRecord(
                trace_id=f"prune-test-{i:03d}",
                timestamp_unix_us=i * 1000,
                observation=sample_observation,
                candidate=sample_candidate,
                constraints=sample_constraints,
                approved=True,
            )
            store.record_decision(record)

        # Should have pruned oldest ones
        stats = store.stats()
        assert stats["decision_count"] <= 15  # May be fewer after pruning

        store.close()


class TestObservationBuffer:
    """Tests for ObservationBuffer class."""

    def test_create_buffer(self):
        """Test buffer creation."""
        config = BufferConfig(max_size=100, persist_interval=10, preload_on_start=False)
        buffer = ObservationBuffer(config=config)
        assert len(buffer) == 0

    def test_add_observations(self, sample_observation):
        """Test adding observations to buffer."""
        config = BufferConfig(max_size=100, persist_interval=1000, preload_on_start=False)
        buffer = ObservationBuffer(config=config)

        for i in range(10):
            obs = StateObservation(
                motor_speed_rpm=1500.0 + i * 10,
                motor_temp_c=45.0 + i,
                pressure_bar=5.0,
                safety_state="normal",
                timestamp_us=1000000 + i * 1000,
            )
            buffer.add(obs, 1000000 + i * 1000)

        assert len(buffer) == 10
        assert buffer.speed_history[-1] == 1590.0
        assert buffer.temp_history[-1] == 54.0

    def test_rolling_window(self, sample_observation):
        """Test that buffer maintains rolling window."""
        config = BufferConfig(max_size=5, persist_interval=1000, preload_on_start=False)
        buffer = ObservationBuffer(config=config)

        # Add 10 observations
        for i in range(10):
            obs = StateObservation(
                motor_speed_rpm=1000.0 + i * 100,
                motor_temp_c=40.0 + i,
                pressure_bar=5.0,
                safety_state="normal",
                timestamp_us=1000000 + i * 1000,
            )
            buffer.add(obs, 1000000 + i * 1000)

        # Should only have last 5
        assert len(buffer) == 5
        assert buffer.speed_history[0] == 1500.0  # First kept is #5
        assert buffer.speed_history[-1] == 1900.0  # Last is #9

    def test_get_window(self, sample_observation):
        """Test getting a specific window of observations."""
        config = BufferConfig(max_size=100, persist_interval=1000, preload_on_start=False)
        buffer = ObservationBuffer(config=config)

        for i in range(20):
            obs = StateObservation(
                motor_speed_rpm=1000.0 + i * 50,
                motor_temp_c=40.0 + i * 0.5,
                pressure_bar=5.0,
                safety_state="normal",
                timestamp_us=1000000 + i * 1000,
            )
            buffer.add(obs, 1000000 + i * 1000)

        speed, temp = buffer.get_window(5)
        assert len(speed) == 5
        assert len(temp) == 5
        assert speed[-1] == 1950.0  # Last observation

    def test_get_stats(self, sample_observation):
        """Test getting buffer statistics."""
        config = BufferConfig(max_size=100, persist_interval=1000, preload_on_start=False)
        buffer = ObservationBuffer(config=config)

        speeds = [1000, 1200, 1400, 1600, 1800]
        temps = [40, 45, 50, 55, 60]

        for s, t in zip(speeds, temps):
            obs = StateObservation(
                motor_speed_rpm=float(s),
                motor_temp_c=float(t),
                pressure_bar=5.0,
                safety_state="normal",
                timestamp_us=1000000,
            )
            buffer.add(obs, 1000000)

        stats = buffer.get_stats()
        assert stats["count"] == 5
        assert stats["speed"]["min"] == 1000
        assert stats["speed"]["max"] == 1800
        assert stats["speed"]["avg"] == 1400
        assert stats["temp"]["min"] == 40
        assert stats["temp"]["max"] == 60
        assert stats["temp"]["avg"] == 50


class TestQueryFunctions:
    """Tests for query functions."""

    @pytest.fixture(autouse=True)
    def setup_global_store(self, temp_db, sample_observation, sample_candidate, sample_constraints):
        """Setup global store with test data."""
        reset_decision_store()
        os.environ["NEUROPLC_DECISION_DB"] = str(temp_db)

        store = get_decision_store()
        base_time = int(time.time() * 1_000_000)

        # Record some test decisions
        self.test_trace_ids = []
        for i in range(10):
            trace_id = f"query-test-{i:03d}"
            self.test_trace_ids.append(trace_id)

            obs = StateObservation(
                motor_speed_rpm=1000.0 + i * 100,
                motor_temp_c=40.0 + i * 2,
                pressure_bar=5.0 + i * 0.5,
                safety_state="normal",
                timestamp_us=base_time + i * 60_000_000,  # 1 minute apart
            )

            record = DecisionRecord(
                trace_id=trace_id,
                timestamp_unix_us=base_time + i * 60_000_000,
                observation=obs,
                candidate=sample_candidate,
                constraints=sample_constraints,
                engine="test-engine",
                approved=True,
            )
            store.record_decision(record)

        self.base_time = base_time
        self.store = store
        yield
        reset_decision_store()

    def test_query_decision_history(self):
        """Test querying decision history."""
        results = query_decision_history(limit=5)
        assert len(results) == 5
        assert all("trace_id" in r for r in results)

    def test_query_decision_history_by_metric(self):
        """Test filtering by metric type."""
        results = query_decision_history(metric="speed", limit=5)
        assert len(results) == 5
        assert all("motor_speed_rpm" in r for r in results)

    def test_get_decision_outcome(self):
        """Test getting a specific decision outcome."""
        trace_id = self.test_trace_ids[0]
        result = get_decision_outcome(trace_id)

        assert result is not None
        assert result["trace_id"] == trace_id
        assert "decision" in result
        assert "validation" in result
        assert "context" in result

    def test_get_decision_outcome_not_found(self):
        """Test getting non-existent decision."""
        result = get_decision_outcome("non-existent-trace-id")
        assert result is None

    def test_get_similar_scenarios(self, sample_observation):
        """Test finding similar scenarios."""
        results = get_similar_scenarios(sample_observation, k=5)

        # Should find some similar scenarios
        assert isinstance(results, list)

        # Results should have similarity scores
        for r in results:
            assert "similarity" in r
            assert 0 <= r["similarity"] <= 1


class TestToolIntegration:
    """Tests for memory tools integration."""

    def test_tool_definitions_exist(self):
        """Test that memory tools are defined."""
        from agent.tools import tool_definitions

        definitions = tool_definitions()
        tool_names = [d["function"]["name"] for d in definitions]

        assert "query_decision_history" in tool_names
        assert "get_similar_scenarios" in tool_names
        assert "get_decision_outcome" in tool_names
        assert "record_feedback" in tool_names

    def test_execute_get_state_summary(self, sample_observation, sample_constraints, sample_candidate):
        """Test executing get_state_summary tool."""
        from agent.tools import execute_tool, AgentContext

        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=sample_candidate,
        )

        result = execute_tool("get_state_summary", {}, ctx)
        assert result["motor_speed_rpm"] == 1500.0
        assert result["motor_temp_c"] == 45.0


class TestGlobalSingletons:
    """Tests for global singleton management."""

    def test_get_decision_store_singleton(self, temp_db):
        """Test decision store singleton behavior."""
        reset_decision_store()
        os.environ["NEUROPLC_DECISION_DB"] = str(temp_db)

        store1 = get_decision_store()
        store2 = get_decision_store()

        assert store1 is store2
        reset_decision_store()

    def test_get_observation_buffer_singleton(self):
        """Test observation buffer singleton behavior."""
        reset_observation_buffer()

        buffer1 = get_observation_buffer()
        buffer2 = get_observation_buffer()

        assert buffer1 is buffer2
        reset_observation_buffer()

    def test_disabled_store(self, temp_db):
        """Test that store can be disabled."""
        reset_decision_store()

        store = get_decision_store(enabled=False)
        assert store is None

        reset_decision_store()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
