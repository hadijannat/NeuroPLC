"""Tests for agent tools."""
from __future__ import annotations

import pytest

from agent.schemas import Constraints, RecommendationCandidate, StateObservation
from agent.tools import (
    AgentContext,
    execute_tool,
    tool_definitions,
    _compute_trend,
    _query_digital_twin,
)


@pytest.fixture
def sample_observation():
    return StateObservation(
        motor_speed_rpm=1500.0,
        motor_temp_c=55.0,
        pressure_bar=5.0,
        safety_state="SAFE",
        cycle_jitter_us=50,
        timestamp_us=1000000,
    )


@pytest.fixture
def sample_constraints():
    return Constraints(
        min_speed_rpm=0.0,
        max_speed_rpm=3000.0,
        max_rate_rpm=50.0,
        max_temp_c=80.0,
    )


@pytest.fixture
def sample_context(sample_observation, sample_constraints):
    return AgentContext(
        obs=sample_observation,
        constraints=sample_constraints,
        last_recommendation=None,
        speed_history=[1400.0, 1420.0, 1440.0, 1460.0, 1480.0, 1500.0],
        temp_history=[52.0, 53.0, 54.0, 54.5, 55.0, 55.0],
        basyx_adapter=None,
    )


class TestToolDefinitions:
    """Test cases for tool definitions."""

    def test_tool_definitions_count(self):
        tools = tool_definitions()
        # Should have 12 tools: original 4 + 3 extended + 4 memory tools + 1 learning tool
        assert len(tools) == 12

    def test_tool_definitions_structure(self):
        tools = tool_definitions()
        for tool in tools:
            assert "type" in tool
            assert tool["type"] == "function"
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]

    def test_tool_names(self):
        tools = tool_definitions()
        names = {t["function"]["name"] for t in tools}
        expected = {
            "get_constraints",
            "get_last_recommendation",
            "get_state_summary",
            "compute_slew_limited_setpoint",
            "get_speed_trend",
            "get_temp_trend",
            "query_digital_twin",
            # Memory tools
            "query_decision_history",
            "get_similar_scenarios",
            "get_decision_outcome",
            "record_feedback",
            # Learning tool
            "get_learning_stats",
        }
        assert names == expected


class TestExecuteTool:
    """Test cases for tool execution."""

    def test_get_constraints(self, sample_context):
        result = execute_tool("get_constraints", {}, sample_context)
        assert result["max_speed_rpm"] == 3000.0
        assert result["min_speed_rpm"] == 0.0
        assert result["max_rate_rpm"] == 50.0
        assert result["max_temp_c"] == 80.0

    def test_get_state_summary(self, sample_context):
        result = execute_tool("get_state_summary", {}, sample_context)
        assert result["motor_speed_rpm"] == 1500.0
        assert result["motor_temp_c"] == 55.0
        assert result["pressure_bar"] == 5.0
        assert result["safety_state"] == "SAFE"

    def test_get_last_recommendation_none(self, sample_context):
        result = execute_tool("get_last_recommendation", {}, sample_context)
        assert result is None

    def test_get_last_recommendation_exists(self, sample_context):
        sample_context.last_recommendation = RecommendationCandidate(
            action="adjust_setpoint",
            target_speed_rpm=1550.0,
            confidence=0.9,
            reasoning="Test",
        )
        result = execute_tool("get_last_recommendation", {}, sample_context)
        assert result["target_speed_rpm"] == 1550.0

    def test_compute_slew_limited_setpoint_within_rate(self, sample_context):
        result = execute_tool(
            "compute_slew_limited_setpoint",
            {"target_speed_rpm": 1530.0},
            sample_context,
        )
        assert result == 1530.0  # Within rate limit

    def test_compute_slew_limited_setpoint_exceeds_rate_up(self, sample_context):
        result = execute_tool(
            "compute_slew_limited_setpoint",
            {"target_speed_rpm": 1600.0},  # 100 RPM change
            sample_context,
        )
        assert result == 1550.0  # Limited to current + max_rate

    def test_compute_slew_limited_setpoint_exceeds_rate_down(self, sample_context):
        result = execute_tool(
            "compute_slew_limited_setpoint",
            {"target_speed_rpm": 1400.0},  # -100 RPM change
            sample_context,
        )
        assert result == 1450.0  # Limited to current - max_rate

    def test_unknown_tool_raises(self, sample_context):
        with pytest.raises(ValueError, match="Unknown tool"):
            execute_tool("unknown_tool", {}, sample_context)


class TestSpeedTrendTool:
    """Test cases for speed trend analysis."""

    def test_get_speed_trend(self, sample_context):
        result = execute_tool("get_speed_trend", {}, sample_context)

        assert result["count"] == 6
        assert result["latest"] == 1500.0
        assert result["min"] == 1400.0
        assert result["max"] == 1500.0
        assert "avg" in result
        assert "slope" in result
        assert result["trend"] == "rising"

    def test_get_speed_trend_with_window(self, sample_context):
        result = execute_tool("get_speed_trend", {"window_size": 3}, sample_context)
        assert result["count"] == 3
        assert result["latest"] == 1500.0

    def test_get_speed_trend_empty_history(self, sample_observation, sample_constraints):
        ctx = AgentContext(
            obs=sample_observation,
            constraints=sample_constraints,
            last_recommendation=None,
            speed_history=[],
            temp_history=[],
        )
        result = execute_tool("get_speed_trend", {}, ctx)
        assert "error" in result
        assert result["count"] == 0


class TestTempTrendTool:
    """Test cases for temperature trend analysis."""

    def test_get_temp_trend(self, sample_context):
        result = execute_tool("get_temp_trend", {}, sample_context)

        assert result["count"] == 6
        assert result["latest"] == 55.0
        assert result["min"] == 52.0
        assert result["max"] == 55.0
        assert "trend" in result


class TestDigitalTwinTool:
    """Test cases for digital twin query."""

    def test_query_digital_twin_fallback(self, sample_context):
        # Without basyx_adapter, should use constraints fallback
        result = execute_tool(
            "query_digital_twin",
            {"property_name": "MaxSpeedRPM"},
            sample_context,
        )
        assert result["property"] == "MaxSpeedRPM"
        assert result["value"] == 3000.0
        assert result["source"] == "constraints_fallback"

    def test_query_digital_twin_min_speed(self, sample_context):
        result = execute_tool(
            "query_digital_twin",
            {"property_name": "MinSpeedRPM"},
            sample_context,
        )
        assert result["value"] == 0.0

    def test_query_digital_twin_max_temp(self, sample_context):
        result = execute_tool(
            "query_digital_twin",
            {"property_name": "MaxTemperatureC"},
            sample_context,
        )
        assert result["value"] == 80.0

    def test_query_digital_twin_safety_level(self, sample_context):
        result = execute_tool(
            "query_digital_twin",
            {"property_name": "SafetyIntegrityLevel"},
            sample_context,
        )
        assert result["value"] == "SIL2"

    def test_query_digital_twin_unknown_property(self, sample_context):
        result = execute_tool(
            "query_digital_twin",
            {"property_name": "UnknownProperty"},
            sample_context,
        )
        assert "error" in result


class TestComputeTrend:
    """Test cases for _compute_trend helper."""

    def test_compute_trend_rising(self):
        history = [100.0, 110.0, 120.0, 130.0, 140.0]
        result = _compute_trend(history, 10, "test")
        assert result["trend"] == "rising"
        assert result["slope"] > 0

    def test_compute_trend_falling(self):
        history = [140.0, 130.0, 120.0, 110.0, 100.0]
        result = _compute_trend(history, 10, "test")
        assert result["trend"] == "falling"
        assert result["slope"] < 0

    def test_compute_trend_stable(self):
        history = [100.0, 100.0, 100.0, 100.0, 100.0]
        result = _compute_trend(history, 10, "test")
        assert result["trend"] == "stable"
        assert result["slope"] == 0.0

    def test_compute_trend_single_value(self):
        history = [100.0]
        result = _compute_trend(history, 10, "test")
        assert result["count"] == 1
        assert result["trend"] == "unknown"
        assert result["std_dev"] == 0.0
