from __future__ import annotations

import json
import os
import time
from typing import Optional

from pydantic import ValidationError

from .schemas import Constraints, RecommendationCandidate, StateObservation

_FAILURES = 0
_LAST_FAILURE_AT = 0.0


class LLMEngineError(Exception):
    pass


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

    engine = LLMEngine()
    try:
        rec = engine.recommend(obs, constraints)
        _FAILURES = 0
        return rec
    except Exception:
        _FAILURES += 1
        _LAST_FAILURE_AT = now
        return None
