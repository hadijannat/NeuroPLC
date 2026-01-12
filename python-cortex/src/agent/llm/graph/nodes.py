"""LangGraph node implementations for NeuroPLC agent workflow."""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from pydantic import BaseModel, ValidationError

from ...schemas import Constraints, RecommendationCandidate, StateObservation
from ...tools import AgentContext, execute_tool, tool_definitions, tool_result_to_message
from ...audit import hash_envelope, hash_tool_call
from ..providers import LLMProvider, ToolCall
from .state import AgentState, CriticFeedback, ToolTrace


# System prompts
PLANNER_SYSTEM_PROMPT = """You are a safety-first industrial motor controller supervisor.

Your task is to recommend a safe motor speed setpoint based on current sensor readings.

SAFETY RULES (MUST FOLLOW):
1. Never exceed max_speed_rpm from constraints
2. Never go below min_speed_rpm from constraints
3. Respect rate-of-change limits (max_rate_rpm per control cycle)
4. If temperature exceeds max_temp_c, recommend speed reduction
5. If uncertain, maintain current speed (action: "hold")

You may call tools to get additional information:
- get_constraints: Get current safety limits
- get_state_summary: Get current sensor readings
- get_last_recommendation: Get previous recommendation
- compute_slew_limited_setpoint: Calculate rate-limited speed change

Return ONLY valid JSON matching the schema. No explanations outside JSON."""

CRITIC_SYSTEM_PROMPT = """You are a strict safety critic for an industrial motor controller.

Review the proposed recommendation and verify it respects ALL safety constraints:
1. Target speed within [min_speed_rpm, max_speed_rpm]
2. Rate of change within max_rate_rpm of current speed
3. Temperature below max_temp_c (if above, speed should decrease)
4. Values are finite (not NaN or Infinity)

Return JSON: {"approved": bool, "reason": string, "violations": [string]}"""


def observe_node(state: AgentState) -> dict[str, Any]:
    """Read observation and prepare context for planning.

    This node validates the observation is fresh and prepares
    the initial messages for the LLM.
    """
    obs = state["observation"]
    constraints = state["constraints"]

    # Build system message
    system_content = PLANNER_SYSTEM_PROMPT

    # Build user message with current state
    user_content = json.dumps({
        "current_state": {
            "motor_speed_rpm": obs.motor_speed_rpm,
            "motor_temp_c": obs.motor_temp_c,
            "pressure_bar": obs.pressure_bar,
            "safety_state": obs.safety_state,
        },
        "constraints_summary": {
            "max_speed_rpm": constraints.max_speed_rpm,
            "min_speed_rpm": constraints.min_speed_rpm,
            "max_rate_rpm": constraints.max_rate_rpm,
            "max_temp_c": constraints.max_temp_c,
        },
        "response_schema": RecommendationCandidate.model_json_schema(),
        "instructions": "Analyze the state and return a JSON recommendation. Use tools if needed.",
    })

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]

    return {
        "messages": messages,
        "step_count": 0,
    }


def plan_node(
    state: AgentState,
    provider: LLMProvider,
    timeout_s: float = 2.0,
    use_structured_output: bool = True,
) -> dict[str, Any]:
    """Generate recommendation using LLM with optional tool calls.

    This is the main reasoning node that calls the LLM.

    Args:
        state: Current agent state
        provider: LLM provider instance
        timeout_s: Request timeout
        use_structured_output: If True, use native JSON schema enforcement
            for final recommendation (when not using tools)
    """
    messages = state["messages"]
    tools = tool_definitions()
    step_count = state.get("step_count", 0) + 1
    max_steps = state.get("max_steps", 5)

    # On final step, don't allow more tool calls - force structured output
    is_final_step = step_count >= max_steps - 1
    should_use_structured = use_structured_output and provider.supports_native_structured_output()

    if is_final_step and should_use_structured:
        # Final step: use structured output without tools
        response = provider.chat(
            messages=messages,
            tools=None,  # No tools on final step
            response_schema=RecommendationCandidate,
            temperature=0.1,
            timeout_s=timeout_s,
        )
    else:
        # Normal step: allow tool calls
        response = provider.chat(
            messages=messages,
            tools=tools,
            temperature=0.1,
            timeout_s=timeout_s,
        )

    if response.has_tool_calls:
        # Return tool calls for processing
        return {
            "plan_output": {
                "type": "tool_calls",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in response.tool_calls
                ],
                "content": response.content,
            },
            "step_count": step_count,
        }

    # Parse final response
    content = response.content or ""
    if not content:
        return {
            "should_fallback": True,
            "error_message": "LLM returned empty response",
            "step_count": step_count,
        }

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        # If using structured output and still got invalid JSON, that's a provider issue
        return {
            "should_fallback": True,
            "error_message": f"Invalid JSON from LLM: {exc}",
            "step_count": step_count,
        }

    return {
        "plan_output": {
            "type": "recommendation",
            "payload": payload,
        },
        "step_count": step_count,
    }


def execute_tools_node(state: AgentState) -> dict[str, Any]:
    """Execute tool calls and update messages.

    This node processes tool calls from the plan node and
    adds results to the message history.
    """
    plan_output = state.get("plan_output")
    if not plan_output or plan_output.get("type") != "tool_calls":
        return {}

    tool_calls = plan_output.get("tool_calls", [])
    messages = list(state.get("messages", []))
    tool_traces = list(state.get("tool_traces", []))

    # Create context for tool execution
    ctx = AgentContext(
        obs=state["observation"],
        constraints=state["constraints"],
        last_recommendation=state.get("last_recommendation"),
        speed_history=state.get("speed_history", []),
        temp_history=state.get("temp_history", []),
        basyx_adapter=state.get("basyx_adapter"),
    )

    # Build assistant message with tool calls
    assistant_msg = {
        "role": "assistant",
        "content": plan_output.get("content"),
        "tool_calls": [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"]),
                },
            }
            for tc in tool_calls
        ],
    }
    messages.append(assistant_msg)

    # Execute each tool and add result
    for tc in tool_calls:
        name = tc["name"]
        args = tc["arguments"]

        try:
            result = execute_tool(name, args, ctx)
        except Exception as exc:
            result = {"error": str(exc)}

        # Record trace for audit
        trace_dict = hash_tool_call(name, args, result)
        tool_traces.append(ToolTrace(
            name=trace_dict["name"],
            args_hash=trace_dict["args_hash"],
            result_hash=trace_dict["result_hash"],
        ))

        # Add tool result to messages
        messages.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "content": tool_result_to_message(result),
        })

    return {
        "messages": messages,
        "tool_traces": tool_traces,
        "plan_output": None,  # Clear for next iteration
    }


def validate_node(state: AgentState) -> dict[str, Any]:
    """Validate the recommendation against constraints.

    This is a deterministic safety check that runs before critic.
    """
    plan_output = state.get("plan_output")
    if not plan_output or plan_output.get("type") != "recommendation":
        return {}

    payload = plan_output.get("payload", {})
    constraints = state["constraints"]
    obs = state["observation"]

    violations = []

    # Parse candidate
    try:
        candidate = RecommendationCandidate.model_validate(payload)
    except ValidationError as exc:
        return {
            "should_fallback": True,
            "error_message": f"Schema validation failed: {exc}",
        }

    target = candidate.target_speed_rpm

    # Check bounds
    if target < constraints.min_speed_rpm:
        violations.append(f"target {target} below min {constraints.min_speed_rpm}")
        target = constraints.min_speed_rpm
    if target > constraints.max_speed_rpm:
        violations.append(f"target {target} above max {constraints.max_speed_rpm}")
        target = constraints.max_speed_rpm

    # Check rate of change
    delta = abs(target - obs.motor_speed_rpm)
    if delta > constraints.max_rate_rpm:
        violations.append(f"rate {delta:.1f} exceeds max {constraints.max_rate_rpm}")
        # Clamp to allowed rate
        if target > obs.motor_speed_rpm:
            target = obs.motor_speed_rpm + constraints.max_rate_rpm
        else:
            target = obs.motor_speed_rpm - constraints.max_rate_rpm

    # Check temperature interlock
    if obs.motor_temp_c > constraints.max_temp_c:
        violations.append(f"temp {obs.motor_temp_c} exceeds max {constraints.max_temp_c}")

    # Update candidate with clamped values
    candidate.target_speed_rpm = target

    if violations:
        # Validation found issues but we can still proceed with clamped values
        return {
            "candidate": candidate,
            "critic_feedback": CriticFeedback(
                approved=False,
                reason="Deterministic validation found violations",
                violations=violations,
            ),
        }

    return {
        "candidate": candidate,
    }


class CriticResponse(BaseModel):
    """Schema for critic LLM response."""
    approved: bool
    reason: str
    violations: list[str] = []


def critic_node(
    state: AgentState,
    provider: LLMProvider,
    timeout_s: float = 2.0,
    use_structured_output: bool = True,
) -> dict[str, Any]:
    """LLM-based critic for additional safety review.

    This is an optional second-pass validation using the LLM.

    Args:
        state: Current agent state
        provider: LLM provider instance
        timeout_s: Request timeout
        use_structured_output: If True, use native JSON schema enforcement
    """
    candidate = state.get("candidate")
    if not candidate:
        return {
            "should_fallback": True,
            "error_message": "No candidate to critique",
        }

    # If deterministic validation already failed, skip LLM critic
    existing_feedback = state.get("critic_feedback")
    if existing_feedback and not existing_feedback.approved:
        # Already have feedback from validate_node
        return {}

    obs = state["observation"]
    constraints = state["constraints"]

    payload = {
        "candidate": candidate.model_dump(),
        "constraints": constraints.model_dump(),
        "current_state": {
            "motor_speed_rpm": obs.motor_speed_rpm,
            "motor_temp_c": obs.motor_temp_c,
            "pressure_bar": obs.pressure_bar,
            "safety_state": obs.safety_state,
        },
    }

    # Use structured output if supported
    should_use_structured = use_structured_output and provider.supports_native_structured_output()

    response = provider.chat(
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_schema=CriticResponse if should_use_structured else None,
        temperature=0.0,
        timeout_s=timeout_s,
    )

    content = response.content or "{}"
    try:
        critic_result = json.loads(content)
    except json.JSONDecodeError:
        # If critic fails, approve by default
        return {
            "critic_feedback": CriticFeedback(
                approved=True,
                reason="Critic parse failed, approving by default",
            ),
        }

    return {
        "critic_feedback": CriticFeedback(
            approved=critic_result.get("approved", True),
            reason=critic_result.get("reason", ""),
            violations=critic_result.get("violations", []),
        ),
    }


def finalize_node(state: AgentState) -> dict[str, Any]:
    """Finalize the recommendation with latency tracking."""
    start_time = state.get("start_time", time.time())
    latency_ms = int((time.time() - start_time) * 1000)

    return {
        "latency_ms": latency_ms,
    }


def fallback_node(state: AgentState) -> dict[str, Any]:
    """Generate fallback recommendation when agent fails.

    Falls back to maintaining current speed with low confidence.
    """
    obs = state["observation"]
    error = state.get("error_message", "Unknown error")

    candidate = RecommendationCandidate(
        action="fallback",
        target_speed_rpm=obs.motor_speed_rpm,
        confidence=0.3,
        reasoning=f"Fallback: {error}",
    )

    start_time = state.get("start_time", time.time())
    latency_ms = int((time.time() - start_time) * 1000)

    return {
        "candidate": candidate,
        "latency_ms": latency_ms,
    }


def should_continue_planning(state: AgentState) -> str:
    """Determine next node after plan_node.

    Returns:
        'execute_tools' if tool calls pending
        'validate' if recommendation ready
        'fallback' if error or max steps
    """
    if state.get("should_fallback"):
        return "fallback"

    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 5)

    if step_count >= max_steps:
        return "fallback"

    plan_output = state.get("plan_output")
    if not plan_output:
        return "fallback"

    if plan_output.get("type") == "tool_calls":
        return "execute_tools"

    if plan_output.get("type") == "recommendation":
        return "validate"

    return "fallback"


def should_continue_after_critic(state: AgentState) -> str:
    """Determine next node after critic.

    Returns:
        'finalize' if approved
        'plan' if rejected and retries left
        'fallback' if rejected and no retries
    """
    if state.get("should_fallback"):
        return "fallback"

    feedback = state.get("critic_feedback")
    if feedback and feedback.approved:
        return "finalize"

    # Check if we can retry
    step_count = state.get("step_count", 0)
    max_steps = state.get("max_steps", 5)

    if step_count < max_steps - 1:  # Reserve one step for retry
        return "plan"

    return "fallback"
