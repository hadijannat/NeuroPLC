"""LangGraph workflow builder for NeuroPLC agent."""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..providers import LLMProvider
from .state import AgentState
from .nodes import (
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


class WorkflowGraph:
    """Simple workflow graph implementation.

    This provides a LangGraph-like interface without requiring the
    langgraph dependency. For full LangGraph features, the actual
    library can be used as a drop-in replacement.
    """

    def __init__(
        self,
        provider: LLMProvider,
        timeout_s: float = 2.0,
        enable_critic: bool = False,
        use_structured_output: bool = True,
    ):
        self.provider = provider
        self.timeout_s = timeout_s
        self.enable_critic = enable_critic
        self.use_structured_output = use_structured_output

    def invoke(self, state: AgentState) -> AgentState:
        """Execute the workflow graph.

        Flow:
        1. observe -> plan
        2. plan -> (execute_tools -> plan) | validate
        3. validate -> critic (if enabled) -> finalize | fallback
        """
        # Step 1: Observe
        state = self._merge_state(state, observe_node(state))

        # Step 2-3: Plan loop
        while True:
            # Plan
            state = self._merge_state(
                state,
                plan_node(
                    state,
                    self.provider,
                    self.timeout_s,
                    use_structured_output=self.use_structured_output,
                )
            )

            # Decide next step
            next_node = should_continue_planning(state)

            if next_node == "fallback":
                state = self._merge_state(state, fallback_node(state))
                return state

            if next_node == "execute_tools":
                state = self._merge_state(state, execute_tools_node(state))
                continue  # Back to plan

            if next_node == "validate":
                break

        # Step 4: Validate
        state = self._merge_state(state, validate_node(state))

        # Step 5: Critic (if enabled)
        if self.enable_critic:
            state = self._merge_state(
                state,
                critic_node(
                    state,
                    self.provider,
                    self.timeout_s,
                    use_structured_output=self.use_structured_output,
                )
            )

            next_node = should_continue_after_critic(state)

            if next_node == "fallback":
                state = self._merge_state(state, fallback_node(state))
                return state

            if next_node == "plan":
                # Retry planning (recursive, but bounded by max_steps)
                return self.invoke(state)

        # Step 6: Finalize
        state = self._merge_state(state, finalize_node(state))
        return state

    def _merge_state(self, state: AgentState, updates: dict[str, Any]) -> AgentState:
        """Merge updates into state."""
        new_state = dict(state)
        new_state.update(updates)
        return AgentState(**new_state)


def build_workflow(
    provider: LLMProvider,
    timeout_s: float = 2.0,
    enable_critic: bool = False,
    use_structured_output: bool = True,
) -> WorkflowGraph:
    """Build a workflow graph with the given configuration.

    Args:
        provider: LLM provider to use
        timeout_s: Timeout for LLM calls
        enable_critic: Whether to enable critic node
        use_structured_output: Whether to use native JSON schema enforcement

    Returns:
        Configured WorkflowGraph
    """
    return WorkflowGraph(
        provider=provider,
        timeout_s=timeout_s,
        enable_critic=enable_critic,
        use_structured_output=use_structured_output,
    )


# Optional: LangGraph integration if available
def build_langgraph_workflow(
    provider: LLMProvider,
    timeout_s: float = 2.0,
    enable_critic: bool = False,
    use_structured_output: bool = True,
):
    """Build workflow using actual LangGraph library.

    This requires langgraph to be installed:
        pip install langgraph

    Args:
        provider: LLM provider to use
        timeout_s: Timeout for LLM calls
        enable_critic: Whether to enable critic node
        use_structured_output: Whether to use native JSON schema enforcement

    Returns:
        Compiled LangGraph StateGraph
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError as exc:
        raise ImportError(
            "langgraph not installed. Run: pip install langgraph"
        ) from exc

    # Create graph
    graph = StateGraph(AgentState)

    # Add nodes with provider bound
    graph.add_node("observe", observe_node)
    graph.add_node(
        "plan",
        lambda s: plan_node(s, provider, timeout_s, use_structured_output=use_structured_output)
    )
    graph.add_node("execute_tools", execute_tools_node)
    graph.add_node("validate", validate_node)
    graph.add_node("finalize", finalize_node)
    graph.add_node("fallback", fallback_node)

    if enable_critic:
        graph.add_node(
            "critic",
            lambda s: critic_node(s, provider, timeout_s, use_structured_output=use_structured_output)
        )

    # Set entry point
    graph.set_entry_point("observe")

    # Add edges
    graph.add_edge("observe", "plan")

    # Conditional edge after plan
    graph.add_conditional_edges(
        "plan",
        should_continue_planning,
        {
            "execute_tools": "execute_tools",
            "validate": "validate",
            "fallback": "fallback",
        }
    )

    graph.add_edge("execute_tools", "plan")

    if enable_critic:
        graph.add_edge("validate", "critic")
        graph.add_conditional_edges(
            "critic",
            should_continue_after_critic,
            {
                "finalize": "finalize",
                "plan": "plan",
                "fallback": "fallback",
            }
        )
    else:
        graph.add_edge("validate", "finalize")

    graph.add_edge("finalize", END)
    graph.add_edge("fallback", END)

    return graph.compile()
