from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from pydantic import ValidationError

from .audit import hash_envelope, hash_tool_call
from .schemas import Constraints, RecommendationCandidate, StateObservation
from .tools import AgentContext, execute_tool, tool_definitions, tool_result_to_message

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

    def recommend(
        self, obs: StateObservation, constraints: Constraints, last: Optional[RecommendationCandidate]
    ) -> LLMOutcome:
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

            latency_ms = int((time.time() - start) * 1000)
            return LLMOutcome(
                candidate=candidate,
                tool_traces=tool_traces,
                llm_output_hash=hash_envelope(payload),
                latency_ms=latency_ms,
                model=self.model,
            )

        raise LLMEngineError("LLM agent exceeded max steps")


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

    engine = LLMEngine()
    try:
        rec = engine.recommend(obs, constraints)
        _FAILURES = 0
        return rec
    except Exception:
        _FAILURES += 1
        _LAST_FAILURE_AT = now
        return None
