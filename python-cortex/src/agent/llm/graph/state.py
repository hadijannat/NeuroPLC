"""LangGraph state definition for NeuroPLC agent workflow."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence, TypedDict

from ...schemas import Constraints, RecommendationCandidate, StateObservation


@dataclass
class ToolTrace:
    """Record of a tool call for audit."""
    name: str
    args_hash: str
    result_hash: str


@dataclass
class CriticFeedback:
    """Feedback from critic node."""
    approved: bool
    reason: str
    violations: list[str] = field(default_factory=list)


class AgentState(TypedDict, total=False):
    """LangGraph state for the recommendation agent.

    This state flows through all nodes in the workflow graph.
    Nodes read from and write to this state.
    """
    # Immutable inputs (set at start)
    observation: StateObservation
    constraints: Constraints
    last_recommendation: Optional[RecommendationCandidate]

    # History for trend analysis
    speed_history: list[float]
    temp_history: list[float]

    # Agent working memory
    messages: list[dict[str, Any]]
    step_count: int
    max_steps: int

    # Planning outputs
    plan_output: Optional[dict[str, Any]]
    tool_traces: list[ToolTrace]

    # Validation outputs
    critic_feedback: Optional[CriticFeedback]

    # Final output
    candidate: Optional[RecommendationCandidate]

    # Control flow
    should_fallback: bool
    error_message: Optional[str]

    # Metadata
    start_time: float
    latency_ms: int

    # Digital twin integration (Optional to avoid circular imports)
    basyx_adapter: Optional[Any]


def create_initial_state(
    observation: StateObservation,
    constraints: Constraints,
    last_recommendation: Optional[RecommendationCandidate] = None,
    speed_history: Optional[list[float]] = None,
    temp_history: Optional[list[float]] = None,
    max_steps: int = 5,
    basyx_adapter: Optional[Any] = None,
) -> AgentState:
    """Create initial state for agent workflow.

    Args:
        observation: Current state observation from sensors.
        constraints: Safety constraints for recommendations.
        last_recommendation: Previous recommendation if available.
        speed_history: Recent speed values for trend analysis.
        temp_history: Recent temperature values for trend analysis.
        max_steps: Maximum planning iterations before fallback.
        basyx_adapter: Optional BaSyx adapter for digital twin queries.
    """
    import time
    return AgentState(
        observation=observation,
        constraints=constraints,
        last_recommendation=last_recommendation,
        speed_history=speed_history or [],
        temp_history=temp_history or [],
        messages=[],
        step_count=0,
        max_steps=max_steps,
        plan_output=None,
        tool_traces=[],
        critic_feedback=None,
        candidate=None,
        should_fallback=False,
        error_message=None,
        start_time=time.time(),
        latency_ms=0,
        basyx_adapter=basyx_adapter,
    )
