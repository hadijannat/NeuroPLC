from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import ValidationError

from .audit import hash_envelope, hash_tool_call
from .schemas import Constraints, RecommendationCandidate, StateObservation
from .tools import AgentContext, execute_tool, tool_definitions, tool_result_to_message

# Provider abstraction
from .llm.providers import (
    LLMProvider,
    ProviderResponse,
    ToolCall,
    create_provider,
    ProviderCreationError,
)

_FAILURES = 0
_LAST_FAILURE_AT = 0.0
_AGENT_FAILURES = 0
_AGENT_LAST_FAILURE_AT = 0.0


class LLMEngineError(Exception):
    pass


@dataclass
class LLMOutcome:
    candidate: RecommendationCandidate
    tool_traces: list[dict]
    llm_output_hash: str
    latency_ms: int
    model: str
    critic: Optional[dict] = None


class LLMEngine:
    def __init__(self, timeout_s: Optional[float] = None) -> None:
        if timeout_s is None:
            timeout_ms = int(os.getenv("NEUROPLC_LLM_TIMEOUT_MS", "800"))
            timeout_s = max(0.05, timeout_ms / 1000.0)
        self.timeout_s = timeout_s
        self.model = os.getenv("NEUROPLC_LLM_MODEL", "gpt-4o-mini")

    def recommend(self, obs: StateObservation, constraints: Constraints) -> RecommendationCandidate:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMEngineError("OPENAI_API_KEY not set")

        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:
            raise LLMEngineError(f"openai import failed: {exc}") from exc

        system = (
            "You are a safety-first industrial supervisor. "
            "Return ONLY JSON that matches the schema. "
            "Never exceed bounds. If uncertain, hold."
        )
        prompt = {
            "observation": obs.model_dump(),
            "constraints": constraints.model_dump(),
            "schema": RecommendationCandidate.model_json_schema(),
        }

        client = OpenAI(api_key=api_key)
        start = time.time()
        response = client.responses.create(
            model=self.model,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt)},
            ],
            temperature=0.1,
            timeout=self.timeout_s,
        )
        elapsed = time.time() - start
        if elapsed > self.timeout_s:
            raise LLMEngineError("LLM timeout exceeded")

        text = response.output_text
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMEngineError(f"Invalid JSON from LLM: {exc}") from exc

        try:
            return RecommendationCandidate.model_validate(payload)
        except ValidationError as exc:
            raise LLMEngineError(f"Schema validation failed: {exc}") from exc


def try_llm_recommendation(
    obs: StateObservation, constraints: Constraints
) -> Optional[RecommendationCandidate]:
    global _FAILURES, _LAST_FAILURE_AT

    threshold = int(os.getenv("NEUROPLC_LLM_FAILURE_THRESHOLD", "5"))
    cooldown_s = float(os.getenv("NEUROPLC_LLM_COOLDOWN_S", "30"))
    now = time.time()

    if _FAILURES >= threshold and (now - _LAST_FAILURE_AT) < cooldown_s:
        return None


class LLMAgentEngine:
    def __init__(self, timeout_s: Optional[float] = None, max_steps: Optional[int] = None) -> None:
        if timeout_s is None:
            timeout_ms = int(os.getenv("NEUROPLC_LLM_TIMEOUT_MS", "800"))
            timeout_s = max(0.05, timeout_ms / 1000.0)
        if max_steps is None:
            max_steps = int(os.getenv("NEUROPLC_LLM_MAX_STEPS", "4"))
        self.timeout_s = timeout_s
        self.max_steps = max(1, max_steps)
        self.model = os.getenv("NEUROPLC_LLM_MODEL", "gpt-4o-mini")
        self.provider_name = os.getenv("NEUROPLC_LLM_PROVIDER", "openai").lower()
        self.enable_critic = os.getenv("NEUROPLC_LLM_ENABLE_CRITIC", "0") in (
            "1",
            "true",
            "yes",
        )
        # Create provider instance (lazy, only if not mock and API key available)
        self._provider: Optional[LLMProvider] = None

    def _get_provider(self) -> LLMProvider:
        """Lazy-load the LLM provider."""
        if self._provider is None:
            try:
                self._provider = create_provider(
                    provider_name=self.provider_name,
                    model=self.model,
                )
            except ProviderCreationError as exc:
                raise LLMEngineError(str(exc)) from exc
        return self._provider

    def recommend(
        self, obs: StateObservation, constraints: Constraints, last: Optional[RecommendationCandidate]
    ) -> LLMOutcome:
        if self.provider_name == "mock":
            candidate = RecommendationCandidate(
                action="adjust_setpoint",
                target_speed_rpm=min(
                    max(obs.motor_speed_rpm + 25.0, constraints.min_speed_rpm),
                    constraints.max_speed_rpm,
                ),
                confidence=0.6,
                reasoning="mock-agent: gentle ramp",
            )
            tool_traces = [
                hash_tool_call("get_state_summary", {}, {"motor_speed_rpm": obs.motor_speed_rpm}),
                hash_tool_call("get_constraints", {}, constraints.model_dump()),
            ]
            payload = candidate.model_dump()
            critic = {"approve": True, "reason": "mock-critic"} if self.enable_critic else None
            return LLMOutcome(
                candidate=candidate,
                tool_traces=tool_traces,
                llm_output_hash=hash_envelope(payload),
                latency_ms=1,
                model="mock-agent",
                critic=critic,
            )

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMEngineError("OPENAI_API_KEY not set")

        try:
            from openai import OpenAI  # type: ignore
        except Exception as exc:
            raise LLMEngineError(f"openai import failed: {exc}") from exc

        system = (
            "You are a safety-first industrial supervisor. "
            "You may call tools to fetch constraints or summarize state. "
            "Return ONLY JSON that matches the schema. If uncertain, hold."
        )
        prompt = {
            "state_summary": {
                "motor_speed_rpm": obs.motor_speed_rpm,
                "motor_temp_c": obs.motor_temp_c,
                "pressure_bar": obs.pressure_bar,
                "safety_state": obs.safety_state,
            },
            "schema": RecommendationCandidate.model_json_schema(),
            "notes": "Use tools if you need constraints or last recommendation.",
        }

        client = OpenAI(api_key=api_key)
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt)},
        ]
        tools = tool_definitions()
        tool_traces: list[dict] = []
        start = time.time()

        ctx = AgentContext(obs=obs, constraints=constraints, last_recommendation=last)

        for _ in range(self.max_steps):
            response = client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
                timeout=self.timeout_s,
            )
            choice = response.choices[0]
            msg = choice.message

            if msg.tool_calls:
                tool_calls = []
                tool_results = []
                for tool_call in msg.tool_calls:
                    name = tool_call.function.name
                    raw_args = tool_call.function.arguments or "{}"
                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}
                    result = execute_tool(name, args, ctx)
                    tool_traces.append(hash_tool_call(name, args, result))
                    tool_calls.append(
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {"name": name, "arguments": raw_args},
                        }
                    )
                    tool_results.append((tool_call.id, result))

                messages.append(
                    {"role": "assistant", "content": msg.content, "tool_calls": tool_calls}
                )
                for tool_call_id, result in tool_results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": tool_result_to_message(result),
                        }
                    )
                continue

            content = msg.content or ""
            if not content:
                raise LLMEngineError("LLM returned empty response")

            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LLMEngineError(f"Invalid JSON from LLM: {exc}") from exc

            try:
                candidate = RecommendationCandidate.model_validate(payload)
            except ValidationError as exc:
                raise LLMEngineError(f"Schema validation failed: {exc}") from exc

            critic = None
            if self.enable_critic:
                critic = self._run_critic(client, obs, constraints, candidate)
                if not critic.get("approve", False):
                    raise LLMEngineError("LLM critic rejected candidate")

            latency_ms = int((time.time() - start) * 1000)
            return LLMOutcome(
                candidate=candidate,
                tool_traces=tool_traces,
                llm_output_hash=hash_envelope(payload),
                latency_ms=latency_ms,
                model=self.model,
                critic=critic,
            )

        raise LLMEngineError("LLM agent exceeded max steps")

    def _run_critic(
        self,
        client: "OpenAI",
        obs: StateObservation,
        constraints: Constraints,
        candidate: RecommendationCandidate,
    ) -> dict:
        system = (
            "You are a strict safety critic. "
            "Approve only if candidate respects constraints and sensor state. "
            "Return JSON: {\"approve\": bool, \"reason\": string}."
        )
        payload = {
            "candidate": candidate.model_dump(),
            "constraints": constraints.model_dump(),
            "state": {
                "motor_speed_rpm": obs.motor_speed_rpm,
                "motor_temp_c": obs.motor_temp_c,
                "pressure_bar": obs.pressure_bar,
                "safety_state": obs.safety_state,
            },
        }
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.0,
            timeout=self.timeout_s,
        )
        content = response.choices[0].message.content or "{}"
        try:
            critic = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMEngineError(f"Critic JSON invalid: {exc}") from exc
        if "approve" not in critic:
            raise LLMEngineError("Critic response missing approve")
        return critic

    def recommend_with_provider(
        self, obs: StateObservation, constraints: Constraints, last: Optional[RecommendationCandidate]
    ) -> LLMOutcome:
        """Provider-based recommend that works with OpenAI, Anthropic, or mock."""
        if self.provider_name == "mock":
            # Reuse existing mock logic
            return self._mock_recommend(obs, constraints)

        provider = self._get_provider()
        system = (
            "You are a safety-first industrial supervisor. "
            "You may call tools to fetch constraints or summarize state. "
            "Return ONLY JSON that matches the schema. If uncertain, hold."
        )
        prompt = {
            "state_summary": {
                "motor_speed_rpm": obs.motor_speed_rpm,
                "motor_temp_c": obs.motor_temp_c,
                "pressure_bar": obs.pressure_bar,
                "safety_state": obs.safety_state,
            },
            "schema": RecommendationCandidate.model_json_schema(),
            "notes": "Use tools if you need constraints or last recommendation.",
        }

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(prompt)},
        ]
        tools = tool_definitions()
        tool_traces: list[dict] = []
        start = time.time()

        ctx = AgentContext(obs=obs, constraints=constraints, last_recommendation=last)

        for _ in range(self.max_steps):
            response = provider.chat(
                messages=messages,
                tools=tools,
                temperature=0.1,
                timeout_s=self.timeout_s,
            )

            if response.has_tool_calls:
                # Process tool calls
                assistant_tool_calls = []
                for tc in response.tool_calls:
                    result = execute_tool(tc.name, tc.arguments, ctx)
                    tool_traces.append(hash_tool_call(tc.name, tc.arguments, result))
                    assistant_tool_calls.append(tc)
                    messages.append(provider.format_tool_result(tc.id, result))

                # Add assistant message with tool calls to history
                messages.insert(-len(response.tool_calls), {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in response.tool_calls
                    ],
                })
                continue

            content = response.content or ""
            if not content:
                raise LLMEngineError("LLM returned empty response")

            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LLMEngineError(f"Invalid JSON from LLM: {exc}") from exc

            try:
                candidate = RecommendationCandidate.model_validate(payload)
            except ValidationError as exc:
                raise LLMEngineError(f"Schema validation failed: {exc}") from exc

            critic = None
            if self.enable_critic:
                critic = self._run_critic_with_provider(provider, obs, constraints, candidate)
                if not critic.get("approve", False):
                    raise LLMEngineError("LLM critic rejected candidate")

            latency_ms = int((time.time() - start) * 1000)
            return LLMOutcome(
                candidate=candidate,
                tool_traces=tool_traces,
                llm_output_hash=hash_envelope(payload),
                latency_ms=latency_ms,
                model=provider.model,
                critic=critic,
            )

        raise LLMEngineError("LLM agent exceeded max steps")

    def _mock_recommend(self, obs: StateObservation, constraints: Constraints) -> LLMOutcome:
        """Generate mock recommendation for testing."""
        candidate = RecommendationCandidate(
            action="adjust_setpoint",
            target_speed_rpm=min(
                max(obs.motor_speed_rpm + 25.0, constraints.min_speed_rpm),
                constraints.max_speed_rpm,
            ),
            confidence=0.6,
            reasoning="mock-agent: gentle ramp",
        )
        tool_traces = [
            hash_tool_call("get_state_summary", {}, {"motor_speed_rpm": obs.motor_speed_rpm}),
            hash_tool_call("get_constraints", {}, constraints.model_dump()),
        ]
        payload = candidate.model_dump()
        critic = {"approve": True, "reason": "mock-critic"} if self.enable_critic else None
        return LLMOutcome(
            candidate=candidate,
            tool_traces=tool_traces,
            llm_output_hash=hash_envelope(payload),
            latency_ms=1,
            model="mock-agent",
            critic=critic,
        )

    def _run_critic_with_provider(
        self,
        provider: LLMProvider,
        obs: StateObservation,
        constraints: Constraints,
        candidate: RecommendationCandidate,
    ) -> dict:
        """Run critic using provider abstraction."""
        system = (
            "You are a strict safety critic. "
            "Approve only if candidate respects constraints and sensor state. "
            'Return JSON: {"approve": bool, "reason": string}.'
        )
        payload = {
            "candidate": candidate.model_dump(),
            "constraints": constraints.model_dump(),
            "state": {
                "motor_speed_rpm": obs.motor_speed_rpm,
                "motor_temp_c": obs.motor_temp_c,
                "pressure_bar": obs.pressure_bar,
                "safety_state": obs.safety_state,
            },
        }
        response = provider.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload)},
            ],
            temperature=0.0,
            timeout_s=self.timeout_s,
        )
        content = response.content or "{}"
        try:
            critic = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMEngineError(f"Critic JSON invalid: {exc}") from exc
        if "approve" not in critic:
            raise LLMEngineError("Critic response missing approve")
        return critic


def try_llm_agent_recommendation_with_provider(
    obs: StateObservation, constraints: Constraints, last: Optional[RecommendationCandidate]
) -> Optional[LLMOutcome]:
    """Provider-based agent recommendation with circuit breaker."""
    global _AGENT_FAILURES, _AGENT_LAST_FAILURE_AT

    threshold = int(os.getenv("NEUROPLC_LLM_FAILURE_THRESHOLD", "5"))
    cooldown_s = float(os.getenv("NEUROPLC_LLM_COOLDOWN_S", "30"))
    now = time.time()

    if _AGENT_FAILURES >= threshold and (now - _AGENT_LAST_FAILURE_AT) < cooldown_s:
        return None

    engine = LLMAgentEngine()
    try:
        outcome = engine.recommend_with_provider(obs, constraints, last)
        _AGENT_FAILURES = 0
        return outcome
    except Exception:
        _AGENT_FAILURES += 1
        _AGENT_LAST_FAILURE_AT = now
        return None


def try_llm_agent_recommendation(
    obs: StateObservation, constraints: Constraints, last: Optional[RecommendationCandidate]
) -> Optional[LLMOutcome]:
    global _AGENT_FAILURES, _AGENT_LAST_FAILURE_AT

    threshold = int(os.getenv("NEUROPLC_LLM_FAILURE_THRESHOLD", "5"))
    cooldown_s = float(os.getenv("NEUROPLC_LLM_COOLDOWN_S", "30"))
    now = time.time()

    if _AGENT_FAILURES >= threshold and (now - _AGENT_LAST_FAILURE_AT) < cooldown_s:
        return None

    engine = LLMAgentEngine()
    try:
        outcome = engine.recommend(obs, constraints, last)
        _AGENT_FAILURES = 0
        return outcome
    except Exception:
        _AGENT_FAILURES += 1
        _AGENT_LAST_FAILURE_AT = now
        return None


# LangGraph-based engine
_LANGGRAPH_FAILURES = 0
_LANGGRAPH_LAST_FAILURE_AT = 0.0


class LangGraphEngine:
    """LangGraph-based recommendation engine with explicit workflow control."""

    def __init__(
        self,
        timeout_s: Optional[float] = None,
        max_steps: Optional[int] = None,
        enable_critic: bool = False,
        enable_cache: bool = True,
    ) -> None:
        if timeout_s is None:
            timeout_ms = int(os.getenv("NEUROPLC_LLM_TIMEOUT_MS", "800"))
            timeout_s = max(0.05, timeout_ms / 1000.0)
        if max_steps is None:
            max_steps = int(os.getenv("NEUROPLC_LLM_MAX_STEPS", "4"))

        self.timeout_s = timeout_s
        self.max_steps = max(1, max_steps)
        self.model = os.getenv("NEUROPLC_LLM_MODEL", "gpt-4o-mini")
        self.provider_name = os.getenv("NEUROPLC_LLM_PROVIDER", "openai").lower()
        self.enable_critic = enable_critic or os.getenv("NEUROPLC_LLM_ENABLE_CRITIC", "0") in (
            "1", "true", "yes"
        )
        self.enable_cache = enable_cache and os.getenv("NEUROPLC_LLM_CACHE_ENABLED", "0") in (
            "1", "true", "yes"
        )
        self.cache_threshold = float(os.getenv("NEUROPLC_LLM_CACHE_THRESHOLD", "0.95"))
        self.cache_ttl = float(os.getenv("NEUROPLC_LLM_CACHE_TTL_S", "60"))
        self._provider: Optional[LLMProvider] = None
        self._workflow = None
        self._cache = None

    def _get_provider(self) -> LLMProvider:
        """Lazy-load the LLM provider."""
        if self._provider is None:
            try:
                self._provider = create_provider(
                    provider_name=self.provider_name,
                    model=self.model,
                )
            except ProviderCreationError as exc:
                raise LLMEngineError(str(exc)) from exc
        return self._provider

    def _get_workflow(self):
        """Lazy-load the workflow graph."""
        if self._workflow is None:
            from .llm.graph import build_workflow
            self._workflow = build_workflow(
                provider=self._get_provider(),
                timeout_s=self.timeout_s,
                enable_critic=self.enable_critic,
            )
        return self._workflow

    def _get_cache(self):
        """Lazy-load the semantic cache."""
        if self._cache is None and self.enable_cache:
            from .llm.cache import SemanticCache
            self._cache = SemanticCache(
                similarity_threshold=self.cache_threshold,
                ttl_s=self.cache_ttl,
            )
        return self._cache

    def recommend(
        self,
        obs: StateObservation,
        constraints: Constraints,
        last: Optional[RecommendationCandidate] = None,
        speed_history: Optional[list[float]] = None,
        temp_history: Optional[list[float]] = None,
        basyx_adapter: Optional[Any] = None,
    ) -> LLMOutcome:
        """Generate recommendation using LangGraph workflow.

        Args:
            obs: Current state observation from sensors.
            constraints: Safety constraints for recommendations.
            last: Previous recommendation if available.
            speed_history: Recent speed values for trend analysis.
            temp_history: Recent temperature values for trend analysis.
            basyx_adapter: Optional BaSyx adapter for digital twin queries.
        """
        from .llm.graph import create_initial_state

        start_time = time.time()

        # Handle mock provider
        if self.provider_name == "mock":
            return self._mock_recommend(obs, constraints)

        # Check cache first
        cache = self._get_cache()
        if cache is not None:
            cached_candidate = cache.lookup(obs, constraints)
            if cached_candidate is not None:
                latency_ms = int((time.time() - start_time) * 1000)
                return LLMOutcome(
                    candidate=cached_candidate,
                    tool_traces=[],
                    llm_output_hash=hash_envelope(cached_candidate.model_dump()),
                    latency_ms=latency_ms,
                    model=f"{self.model}-cached",
                    critic=None,
                )

        # Create initial state
        initial_state = create_initial_state(
            observation=obs,
            constraints=constraints,
            last_recommendation=last,
            speed_history=speed_history,
            temp_history=temp_history,
            max_steps=self.max_steps,
            basyx_adapter=basyx_adapter,
        )

        # Run workflow
        workflow = self._get_workflow()
        final_state = workflow.invoke(initial_state)

        # Extract result
        candidate = final_state.get("candidate")
        if not candidate:
            raise LLMEngineError("Workflow produced no candidate")

        # Store result in cache for future similar queries
        if cache is not None:
            cache.store(obs, constraints, candidate)

        tool_traces = final_state.get("tool_traces", [])
        latency_ms = final_state.get("latency_ms", 0)
        critic_feedback = final_state.get("critic_feedback")

        critic = None
        if critic_feedback:
            critic = {
                "approve": critic_feedback.approved,
                "reason": critic_feedback.reason,
                "violations": critic_feedback.violations,
            }

        return LLMOutcome(
            candidate=candidate,
            tool_traces=[
                {"name": t.name, "args_hash": t.args_hash, "result_hash": t.result_hash}
                for t in tool_traces
            ],
            llm_output_hash=hash_envelope(candidate.model_dump()),
            latency_ms=latency_ms,
            model=self._get_provider().model,
            critic=critic,
        )

    def _mock_recommend(self, obs: StateObservation, constraints: Constraints) -> LLMOutcome:
        """Generate mock recommendation for testing."""
        candidate = RecommendationCandidate(
            action="adjust_setpoint",
            target_speed_rpm=min(
                max(obs.motor_speed_rpm + 25.0, constraints.min_speed_rpm),
                constraints.max_speed_rpm,
            ),
            confidence=0.6,
            reasoning="langgraph-mock: gentle ramp",
        )
        return LLMOutcome(
            candidate=candidate,
            tool_traces=[],
            llm_output_hash=hash_envelope(candidate.model_dump()),
            latency_ms=1,
            model="mock-langgraph",
            critic=None,
        )


def try_langgraph_recommendation(
    obs: StateObservation,
    constraints: Constraints,
    last: Optional[RecommendationCandidate] = None,
    speed_history: Optional[list[float]] = None,
    temp_history: Optional[list[float]] = None,
    basyx_adapter: Optional[Any] = None,
) -> Optional[LLMOutcome]:
    """LangGraph-based recommendation with circuit breaker.

    Args:
        obs: Current state observation from sensors.
        constraints: Safety constraints for recommendations.
        last: Previous recommendation if available.
        speed_history: Recent speed values for trend analysis.
        temp_history: Recent temperature values for trend analysis.
        basyx_adapter: Optional BaSyx adapter for digital twin queries.
    """
    global _LANGGRAPH_FAILURES, _LANGGRAPH_LAST_FAILURE_AT

    threshold = int(os.getenv("NEUROPLC_LLM_FAILURE_THRESHOLD", "5"))
    cooldown_s = float(os.getenv("NEUROPLC_LLM_COOLDOWN_S", "30"))
    now = time.time()

    if _LANGGRAPH_FAILURES >= threshold and (now - _LANGGRAPH_LAST_FAILURE_AT) < cooldown_s:
        return None

    engine = LangGraphEngine()
    try:
        outcome = engine.recommend(
            obs, constraints, last, speed_history, temp_history, basyx_adapter
        )
        _LANGGRAPH_FAILURES = 0
        return outcome
    except Exception:
        _LANGGRAPH_FAILURES += 1
        _LANGGRAPH_LAST_FAILURE_AT = now
        return None
