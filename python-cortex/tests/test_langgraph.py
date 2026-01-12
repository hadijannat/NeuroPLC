"""Tests for LangGraph workflow engine."""
from __future__ import annotations

import json
import pytest
from unittest.mock import MagicMock, patch

from agent.llm.providers import MockProvider, ProviderResponse, ToolCall
from agent.llm.graph import (
    AgentState,
    CriticFeedback,
    ToolTrace,
    create_initial_state,
    WorkflowGraph,
    build_workflow,
)
from agent.llm.graph.nodes import (
    observe_node,
    plan_node,
    execute_tools_node,
    validate_node,
    critic_node,
    finalize_node,
    fallback_node,
    should_continue_planning,
    should_continue_after_critic,
)
from agent.schemas import Constraints, RecommendationCandidate, StateObservation


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
def initial_state(sample_observation, sample_constraints):
    return create_initial_state(
        observation=sample_observation,
        constraints=sample_constraints,
        max_steps=5,
    )


class TestCreateInitialState:
    """Test cases for state initialization."""

    def test_create_initial_state_basic(self, sample_observation, sample_constraints):
        state = create_initial_state(
            observation=sample_observation,
            constraints=sample_constraints,
        )

        assert state["observation"] == sample_observation
        assert state["constraints"] == sample_constraints
        assert state["max_steps"] == 5
        assert state["step_count"] == 0
        assert state["should_fallback"] is False
        assert state["messages"] == []
        assert state["tool_traces"] == []

    def test_create_initial_state_with_history(self, sample_observation, sample_constraints):
        state = create_initial_state(
            observation=sample_observation,
            constraints=sample_constraints,
            speed_history=[1400.0, 1500.0],
            temp_history=[54.0, 55.0],
        )

        assert state["speed_history"] == [1400.0, 1500.0]
        assert state["temp_history"] == [54.0, 55.0]


class TestObserveNode:
    """Test cases for observe_node."""

    def test_observe_node_creates_messages(self, initial_state):
        result = observe_node(initial_state)

        assert "messages" in result
        assert len(result["messages"]) == 2
        assert result["messages"][0]["role"] == "system"
        assert result["messages"][1]["role"] == "user"

    def test_observe_node_includes_state_in_user_message(self, initial_state):
        result = observe_node(initial_state)

        user_content = json.loads(result["messages"][1]["content"])
        assert "current_state" in user_content
        assert user_content["current_state"]["motor_speed_rpm"] == 1500.0


class TestPlanNode:
    """Test cases for plan_node."""

    def test_plan_node_with_recommendation(self, initial_state):
        provider = MockProvider()
        valid_response = ProviderResponse(
            content=json.dumps({
                "action": "adjust_setpoint",
                "target_speed_rpm": 1550.0,
                "confidence": 0.9,
                "reasoning": "Test recommendation",
            }),
            model="mock",
        )
        provider.queue_response(valid_response)

        # First run observe to get messages
        initial_state = dict(initial_state)
        initial_state.update(observe_node(initial_state))

        result = plan_node(initial_state, provider)

        assert result["plan_output"]["type"] == "recommendation"
        assert result["plan_output"]["payload"]["target_speed_rpm"] == 1550.0
        assert result["step_count"] == 1

    def test_plan_node_with_tool_calls(self, initial_state):
        provider = MockProvider()
        tool_response = ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(id="call_1", name="get_constraints", arguments={}),
            ],
            model="mock",
        )
        provider.queue_response(tool_response)

        initial_state = dict(initial_state)
        initial_state.update(observe_node(initial_state))

        result = plan_node(initial_state, provider)

        assert result["plan_output"]["type"] == "tool_calls"
        assert len(result["plan_output"]["tool_calls"]) == 1

    def test_plan_node_empty_response_fallback(self, initial_state):
        provider = MockProvider()
        empty_response = ProviderResponse(content="", model="mock")
        provider.queue_response(empty_response)

        initial_state = dict(initial_state)
        initial_state.update(observe_node(initial_state))

        result = plan_node(initial_state, provider)

        assert result["should_fallback"] is True
        assert "empty response" in result["error_message"].lower()


class TestValidateNode:
    """Test cases for validate_node."""

    def test_validate_node_valid_recommendation(self, initial_state):
        state = dict(initial_state)
        state["plan_output"] = {
            "type": "recommendation",
            "payload": {
                "action": "adjust_setpoint",
                "target_speed_rpm": 1550.0,  # Within bounds and rate
                "confidence": 0.9,
                "reasoning": "Test",
            },
        }

        result = validate_node(state)

        assert "candidate" in result
        assert result["candidate"].target_speed_rpm == 1550.0
        assert "critic_feedback" not in result or result.get("critic_feedback") is None

    def test_validate_node_exceeds_max_speed(self, initial_state):
        state = dict(initial_state)
        state["plan_output"] = {
            "type": "recommendation",
            "payload": {
                "action": "adjust_setpoint",
                "target_speed_rpm": 5000.0,  # Exceeds max
                "confidence": 0.9,
                "reasoning": "Test",
            },
        }

        result = validate_node(state)

        # First clamped to max (3000), then rate-limited from 1500 to 1550
        assert result["candidate"].target_speed_rpm == 1550.0
        assert result["critic_feedback"].approved is False
        assert any("above max" in v for v in result["critic_feedback"].violations)

    def test_validate_node_exceeds_rate(self, initial_state):
        state = dict(initial_state)
        state["plan_output"] = {
            "type": "recommendation",
            "payload": {
                "action": "adjust_setpoint",
                "target_speed_rpm": 1600.0,  # 100 RPM change, exceeds 50
                "confidence": 0.9,
                "reasoning": "Test",
            },
        }

        result = validate_node(state)

        # Should be clamped to 1500 + 50 = 1550
        assert result["candidate"].target_speed_rpm == 1550.0
        assert any("rate" in v for v in result["critic_feedback"].violations)


class TestFallbackNode:
    """Test cases for fallback_node."""

    def test_fallback_node_maintains_speed(self, initial_state):
        state = dict(initial_state)
        state["error_message"] = "Test error"

        result = fallback_node(state)

        assert result["candidate"].action == "fallback"
        assert result["candidate"].target_speed_rpm == 1500.0  # Current speed
        assert result["candidate"].confidence == 0.3
        assert "Test error" in result["candidate"].reasoning


class TestConditionalEdges:
    """Test cases for conditional edge functions."""

    def test_should_continue_planning_fallback(self, initial_state):
        state = dict(initial_state)
        state["should_fallback"] = True

        assert should_continue_planning(state) == "fallback"

    def test_should_continue_planning_max_steps(self, initial_state):
        state = dict(initial_state)
        state["step_count"] = 5
        state["max_steps"] = 5

        assert should_continue_planning(state) == "fallback"

    def test_should_continue_planning_tool_calls(self, initial_state):
        state = dict(initial_state)
        state["plan_output"] = {"type": "tool_calls", "tool_calls": []}

        assert should_continue_planning(state) == "execute_tools"

    def test_should_continue_planning_recommendation(self, initial_state):
        state = dict(initial_state)
        state["plan_output"] = {"type": "recommendation", "payload": {}}

        assert should_continue_planning(state) == "validate"

    def test_should_continue_after_critic_approved(self, initial_state):
        state = dict(initial_state)
        state["critic_feedback"] = CriticFeedback(approved=True, reason="OK")

        assert should_continue_after_critic(state) == "finalize"

    def test_should_continue_after_critic_retry(self, initial_state):
        state = dict(initial_state)
        state["step_count"] = 2
        state["max_steps"] = 5
        state["critic_feedback"] = CriticFeedback(approved=False, reason="Bad")

        assert should_continue_after_critic(state) == "plan"


class TestWorkflowGraph:
    """Test cases for WorkflowGraph."""

    def test_workflow_basic_recommendation(self, sample_observation, sample_constraints):
        provider = MockProvider()
        valid_response = ProviderResponse(
            content=json.dumps({
                "action": "adjust_setpoint",
                "target_speed_rpm": 1525.0,
                "confidence": 0.9,
                "reasoning": "Safe speed increase",
            }),
            model="mock",
        )
        provider.queue_response(valid_response)

        workflow = build_workflow(provider=provider, timeout_s=5.0)
        state = create_initial_state(
            observation=sample_observation,
            constraints=sample_constraints,
        )

        final_state = workflow.invoke(state)

        assert final_state["candidate"] is not None
        assert final_state["candidate"].target_speed_rpm == 1525.0
        assert final_state["latency_ms"] >= 0

    def test_workflow_with_tool_call(self, sample_observation, sample_constraints):
        provider = MockProvider()

        # First response: tool call
        tool_response = ProviderResponse(
            content=None,
            tool_calls=[
                ToolCall(id="call_1", name="get_constraints", arguments={}),
            ],
            model="mock",
        )
        # Second response: final recommendation
        final_response = ProviderResponse(
            content=json.dumps({
                "action": "adjust_setpoint",
                "target_speed_rpm": 1530.0,
                "confidence": 0.95,
                "reasoning": "After checking constraints",
            }),
            model="mock",
        )
        provider.queue_response(tool_response)
        provider.queue_response(final_response)

        workflow = build_workflow(provider=provider)
        state = create_initial_state(
            observation=sample_observation,
            constraints=sample_constraints,
        )

        final_state = workflow.invoke(state)

        assert final_state["candidate"] is not None
        assert len(final_state["tool_traces"]) > 0

    def test_workflow_fallback_on_error(self, sample_observation, sample_constraints):
        provider = MockProvider()
        invalid_response = ProviderResponse(content="not json", model="mock")
        provider.queue_response(invalid_response)

        workflow = build_workflow(provider=provider)
        state = create_initial_state(
            observation=sample_observation,
            constraints=sample_constraints,
        )

        final_state = workflow.invoke(state)

        # Should fallback
        assert final_state["candidate"] is not None
        assert final_state["candidate"].action == "fallback"


class TestBuildWorkflow:
    """Test cases for build_workflow factory."""

    def test_build_workflow_default(self):
        provider = MockProvider()
        workflow = build_workflow(provider=provider)

        assert isinstance(workflow, WorkflowGraph)
        assert workflow.enable_critic is False

    def test_build_workflow_with_critic(self):
        provider = MockProvider()
        workflow = build_workflow(provider=provider, enable_critic=True)

        assert workflow.enable_critic is True

    def test_build_workflow_with_structured_output(self):
        provider = MockProvider()
        workflow = build_workflow(
            provider=provider,
            use_structured_output=True,
        )

        assert workflow.use_structured_output is True
