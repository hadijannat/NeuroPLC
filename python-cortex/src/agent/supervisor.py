import argparse
import base64
import json
import os
import socket
import time
import hashlib
import hmac
import secrets
import uuid
from typing import Optional

from digital_twin import BasyxAdapter, BasyxConfig
from pathlib import Path
from agent.schemas import Constraints, RecommendationCandidate, StateObservation
from agent.safety_validator import materialize_recommendation
from agent.audit import hash_envelope
from agent.llm_engine import (
    try_llm_agent_recommendation,
    try_llm_agent_recommendation_with_provider,
    try_llm_recommendation,
    try_langgraph_recommendation,
)
from agent.memory import (
    DecisionRecord,
    get_decision_store,
    get_observation_buffer,
)
try:
    from agent.ml_inference import MLRecommendationEngine, SafetyBoundedRecommender
except ImportError:
    print("Warning: ML modules not found. Running in rule-based mode.")
    MLRecommendationEngine = None
    SafetyBoundedRecommender = None

def compute_recommendation(
    obs: StateObservation, 
    attack_mode: bool, 
    cycle: int,
    recommender: Optional[object] = None
) -> tuple[RecommendationCandidate, dict]:
    speed = float(obs.motor_speed_rpm)
    temp = float(obs.motor_temp_c)
    pressure = float(obs.pressure_bar)
    
    # Track history (mock for now, in prod use a sliding window buffer)
    speed_hist = [speed] 
    temp_hist = [temp]

    warmup_cycles = int(os.getenv("NEUROPLC_WARMUP_CYCLES", "5"))
    if cycle <= warmup_cycles:
        candidate = RecommendationCandidate(
            action="hold",
            target_speed_rpm=speed,
            confidence=1.0,
            reasoning="warmup: hold speed",
        )
        envelope = {
            "analysis": candidate.reasoning,
            "target_speed_rpm": candidate.target_speed_rpm,
            "model": "warmup-v1",
        }
    elif attack_mode and cycle % 10 == 0:
        candidate = RecommendationCandidate(
            action="adjust_setpoint",
            target_speed_rpm=5000.0,
            confidence=0.2,
            reasoning="attack_mode: requesting unsafe speed to test firewall",
        )
        envelope = {
            "analysis": candidate.reasoning,
            "target_speed_rpm": candidate.target_speed_rpm,
            "model": "attack-v1",
        }
    elif recommender:
        # ML Inference
        target, confidence, envelope = recommender.recommend(
            speed, temp, pressure, speed_hist, temp_hist
        )
        analysis = f"ML-based recommendation (conf={confidence:.2f})"
        envelope["analysis"] = analysis
        envelope["model"] = "onnx-v1"
        candidate = RecommendationCandidate(
            action="adjust_setpoint",
            target_speed_rpm=float(target),
            confidence=float(confidence),
            reasoning=analysis,
        )
    else:
        # Rule-based fallback
        if temp > 70.0:
            target = max(speed - 200.0, 0.0)
            confidence = 0.9
            analysis = "temperature high; reduce speed"
        elif temp < 50.0 and speed < 2000.0:
            target = min(speed + 200.0, 3000.0)
            confidence = 0.85
            analysis = "temperature low; increase speed"
        else:
            target = speed
            confidence = 0.8
            analysis = "maintain speed"
            
        envelope = {
            "analysis": analysis,
            "target_speed_rpm": target,
            "model": "rule-v1",
        }
        candidate = RecommendationCandidate(
            action="adjust_setpoint",
            target_speed_rpm=float(target),
            confidence=float(confidence),
            reasoning=analysis,
        )

    max_rate = float(os.getenv("NEUROPLC_MAX_RATE_RPM", "50"))
    rate_limit_enabled = os.getenv("NEUROPLC_DISABLE_RATE_LIMIT", "0") not in ("1", "true", "yes")
    if rate_limit_enabled:
        delta = candidate.target_speed_rpm - speed
        if abs(delta) > max_rate:
            candidate.target_speed_rpm = speed + (max_rate if delta > 0 else -max_rate)
            candidate.reasoning = f"{candidate.reasoning}; rate_limited"
            envelope["target_speed_rpm"] = candidate.target_speed_rpm
            envelope["analysis"] = candidate.reasoning

    # Common envelope fields
    envelope["state"] = {
        "motor_speed_rpm": speed,
        "motor_temp_c": temp,
        "pressure_bar": pressure,
        "timestamp_us": int(obs.timestamp_us),
    }
    
    return candidate, envelope


def run(host: str, port: int, attack_mode: bool, model_path: Optional[str] = None):
    # Initialize ML engine
    recommender = None
    if model_path and MLRecommendationEngine:
        p = Path(model_path)
        if p.exists():
            try:
                print(f"Loading ML model from {p}...")
                engine = MLRecommendationEngine(p)
                recommender = SafetyBoundedRecommender(engine)
                print("ML model loaded successfully.")
            except Exception as e:
                print(f"Failed to load ML model: {e}")
        else:
            print(f"Model file not found: {p}")
            
    addr = (host, port)
    cycle = 0
    basyx_adapter = None
    basyx_ready = False
    basyx_last_update = 0.0
    basyx_interval_s = float(os.getenv("BASYX_UPDATE_INTERVAL", "1.0"))

    basyx_url = os.getenv("BASYX_URL")
    if basyx_url:
        basyx_config = BasyxConfig(
            base_url=basyx_url,
            aas_id=os.getenv("BASYX_AAS_ID", "urn:neuroplc:aas:motor:001"),
            asset_id=os.getenv("BASYX_ASSET_ID", "urn:neuroplc:asset:motor:001"),
        )
        basyx_adapter = BasyxAdapter(basyx_config)

    # Initialize memory system
    memory_enabled = os.getenv("NEUROPLC_MEMORY_ENABLED", "1") in ("1", "true", "yes")
    decision_store = get_decision_store(enabled=memory_enabled)
    observation_buffer = get_observation_buffer() if memory_enabled else None
    if memory_enabled:
        print(f"Memory system enabled (DB: {decision_store.db_path})")

    auth_secret = os.getenv("NEUROPLC_AUTH_SECRET")
    auth_issuer = os.getenv("NEUROPLC_AUTH_ISSUER", "neuroplc")
    auth_audience = os.getenv("NEUROPLC_AUTH_AUDIENCE", "neuroplc-spine")
    auth_scope = os.getenv("NEUROPLC_AUTH_SCOPE", "cortex:recommend")
    auth_max_age = int(os.getenv("NEUROPLC_AUTH_MAX_AGE", "300"))
    send_hello = os.getenv("NEUROPLC_SEND_HELLO", "0") in ("1", "true", "yes")
    inference_engine = os.getenv("NEUROPLC_INFERENCE_ENGINE", "baseline").lower()
    decision_period_ms = int(os.getenv("NEUROPLC_LLM_DECISION_PERIOD_MS", "500"))
    audit_path = os.getenv("NEUROPLC_CORTEX_AUDIT_PATH")
    constraints = Constraints(
        min_speed_rpm=float(os.getenv("NEUROPLC_MIN_SPEED_RPM", "0")),
        max_speed_rpm=float(os.getenv("NEUROPLC_MAX_SPEED_RPM", "3000")),
        max_rate_rpm=float(os.getenv("NEUROPLC_MAX_RATE_RPM", "50")),
        max_temp_c=float(os.getenv("NEUROPLC_MAX_TEMP_C", "80")),
        staleness_us=int(os.getenv("NEUROPLC_STATE_STALE_US", "500000")),
    )

    def _b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    def _auth_token(now_s: int) -> str:
        claims = {
            "iss": auth_issuer,
            "sub": "python-cortex",
            "aud": auth_audience,
            "scope": [auth_scope],
            "iat": now_s,
            "exp": now_s + auth_max_age,
            "nonce": secrets.token_hex(16),
        }
        payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
        signature = hmac.new(auth_secret.encode("utf-8"), payload, hashlib.sha256).digest()
        return f"{_b64url(payload)}.{_b64url(signature)}"

    last_llm_at = 0.0
    last_llm_candidate = None
    last_llm_meta = None

    while True:
        try:
            with socket.create_connection(addr, timeout=5) as sock:
                sock.settimeout(1.0)
                file = sock.makefile("rwb")
                print(f"Connected to spine at {host}:{port}")
                if send_hello:
                    hello = {
                        "type": "hello",
                        "protocol_version": {"major": 1, "minor": 0},
                        "capabilities": ["recommendation.v1", "auth.hmac-sha256"],
                        "client_id": "python-cortex",
                    }
                    file.write((json.dumps(hello) + "\n").encode("utf-8"))
                    file.flush()
                sequence = 0
                while True:
                    line = file.readline()
                    if not line:
                        break
                    try:
                        state = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    if state.get("type") != "state":
                        continue

                    cycle += 1
                    obs = StateObservation.model_validate(state)
                    if basyx_adapter and not basyx_ready:
                        try:
                            basyx_adapter.ensure_models()
                            basyx_ready = True
                            print("BaSyx: AAS and submodels initialized")
                        except OSError as exc:
                            print(f"BaSyx init failed: {exc}")

                    now_unix_us = int(time.time() * 1_000_000)
                    if obs.unix_us:
                        is_stale = (now_unix_us - int(obs.unix_us)) > constraints.staleness_us
                    else:
                        is_stale = False
                    trace_id = uuid.uuid4().hex

                    obs_hash = hash_envelope({"observation": obs.model_dump()})
                    constraints_hash = hash_envelope({"constraints": constraints.model_dump()})

                    candidate = None
                    if inference_engine == "llm" and not is_stale:
                        candidate = try_llm_recommendation(obs, constraints)
                    elif inference_engine == "llm-agent" and not is_stale:
                        now = time.time()
                        if (now - last_llm_at) * 1000.0 >= decision_period_ms:
                            last_llm_meta = try_llm_agent_recommendation(
                                obs, constraints, last_llm_candidate
                            )
                            if last_llm_meta:
                                last_llm_candidate = last_llm_meta.candidate
                                last_llm_at = now
                        if last_llm_candidate:
                            candidate = last_llm_candidate
                    elif inference_engine == "llm-provider" and not is_stale:
                        # Provider-based agent (supports OpenAI and Anthropic)
                        now = time.time()
                        if (now - last_llm_at) * 1000.0 >= decision_period_ms:
                            last_llm_meta = try_llm_agent_recommendation_with_provider(
                                obs, constraints, last_llm_candidate
                            )
                            if last_llm_meta:
                                last_llm_candidate = last_llm_meta.candidate
                                last_llm_at = now
                        if last_llm_candidate:
                            candidate = last_llm_candidate
                    elif inference_engine == "langgraph" and not is_stale:
                        # LangGraph workflow engine
                        now = time.time()
                        if (now - last_llm_at) * 1000.0 >= decision_period_ms:
                            # Use observation buffer history if available
                            speed_hist = observation_buffer.speed_history if observation_buffer else []
                            temp_hist = observation_buffer.temp_history if observation_buffer else []
                            last_llm_meta = try_langgraph_recommendation(
                                obs, constraints, last_llm_candidate,
                                speed_history=speed_hist, temp_history=temp_hist,
                                basyx_adapter=basyx_adapter,
                            )
                            if last_llm_meta:
                                last_llm_candidate = last_llm_meta.candidate
                                last_llm_at = now
                        if last_llm_candidate:
                            candidate = last_llm_candidate

                    if candidate is None:
                        candidate, envelope = compute_recommendation(
                            obs, attack_mode, cycle, recommender
                        )
                        envelope["engine"] = "baseline"
                    else:
                        envelope = {
                            "analysis": candidate.reasoning,
                            "target_speed_rpm": candidate.target_speed_rpm,
                            "model": "llm",
                        }
                        envelope["engine"] = inference_engine
                        if inference_engine == "llm-agent" and last_llm_meta:
                            envelope["model"] = last_llm_meta.model
                            envelope["llm_latency_ms"] = last_llm_meta.latency_ms
                            envelope["llm_output_hash"] = last_llm_meta.llm_output_hash
                            envelope["tool_traces"] = last_llm_meta.tool_traces
                            if last_llm_meta.critic is not None:
                                envelope["critic"] = last_llm_meta.critic

                    if is_stale:
                        candidate = RecommendationCandidate(
                            action="fallback",
                            target_speed_rpm=obs.motor_speed_rpm,
                            confidence=0.0,
                            reasoning="stale observation",
                        )
                        envelope = {
                            "analysis": "stale observation",
                            "target_speed_rpm": obs.motor_speed_rpm,
                            "model": "stale",
                            "engine": "fallback",
                        }

                    rec = materialize_recommendation(candidate, obs, constraints, trace_id)
                    envelope["observation_hash"] = obs_hash
                    envelope["constraints_hash"] = constraints_hash
                    envelope["candidate"] = candidate.model_dump()
                    envelope["trace_id"] = trace_id
                    envelope["approved"] = rec.approved
                    envelope["violations"] = rec.violations
                    envelope["warnings"] = rec.warnings
                    reasoning_hash = hash_envelope(envelope)
                    if audit_path:
                        audit_entry = {
                            "trace_id": trace_id,
                            "engine": envelope.get("engine", "baseline"),
                            "model": envelope.get("model"),
                            "llm_output_hash": envelope.get("llm_output_hash"),
                            "tool_traces": envelope.get("tool_traces", []),
                            "critic": envelope.get("critic"),
                            "observation_hash": obs_hash,
                            "constraints_hash": constraints_hash,
                            "approved": rec.approved,
                            "violations": rec.violations,
                            "warnings": rec.warnings,
                            "reasoning_hash": reasoning_hash,
                        }
                        try:
                            with open(audit_path, "a", encoding="utf-8") as audit_file:
                                audit_file.write(
                                    json.dumps(
                                        audit_entry,
                                        separators=(",", ":"),
                                        ensure_ascii=True,
                                    )
                                    + "\n"
                                )
                        except OSError:
                            pass

                    # Record to memory system
                    if observation_buffer is not None:
                        observation_buffer.add(obs, now_unix_us)

                    if decision_store is not None:
                        try:
                            decision_record = DecisionRecord(
                                trace_id=trace_id,
                                timestamp_unix_us=now_unix_us,
                                observation=obs,
                                candidate=candidate,
                                constraints=constraints,
                                engine=envelope.get("engine", "baseline"),
                                model=envelope.get("model"),
                                llm_latency_ms=envelope.get("llm_latency_ms"),
                                llm_output_hash=envelope.get("llm_output_hash"),
                                approved=rec.approved,
                                violations=rec.violations,
                                warnings=rec.warnings,
                                tool_traces=envelope.get("tool_traces", []),
                            )
                            decision_store.record_decision(decision_record)
                        except Exception as e:
                            # Non-critical, fail silently but log once
                            if cycle == 1:
                                print(f"Memory recording failed: {e}")

                    sequence += 1
                    issued_at_unix_us = int(time.time() * 1_000_000)
                    msg = {
                        "type": "recommendation",
                        "protocol_version": {"major": 1, "minor": 0},
                        "sequence": sequence,
                        "issued_at_unix_us": issued_at_unix_us,
                        "ttl_ms": 1000,
                        "target_speed_rpm": rec.target_speed_rpm if rec.approved else None,
                        "confidence": rec.confidence if rec.approved else 0.0,
                        "reasoning_hash": reasoning_hash,
                        "client_unix_us": issued_at_unix_us,
                    }
                    if auth_secret:
                        msg["auth_token"] = _auth_token(int(time.time()))
                    file.write((json.dumps(msg) + "\n").encode("utf-8"))
                    file.flush()

                    now = time.time()
                    if basyx_adapter and basyx_ready and (now - basyx_last_update) >= basyx_interval_s:
                        speed = float(state.get("motor_speed_rpm", 0.0))
                        temp = float(state.get("motor_temp_c", 0.0))
                        is_healthy = speed >= 0.0 and temp < 120.0 and all(
                            map(lambda v: v == v and v not in (float("inf"), float("-inf")), [speed, temp])
                        )
                        try:
                            basyx_adapter.update_operational(
                                state,
                                cycle_count=cycle,
                                is_healthy=is_healthy,
                            )
                            if candidate is not None:
                                basyx_adapter.update_recommendation(
                                    target_speed=candidate.target_speed_rpm,
                                    confidence=candidate.confidence,
                                    reasoning_hash=reasoning_hash,
                                )
                            basyx_last_update = now
                        except OSError as exc:
                            print(f"BaSyx update failed: {exc}")
        except (OSError, ConnectionError) as exc:
            print(f"Connection failed: {exc}. Retrying...")
            time.sleep(1.0)


def main():
    parser = argparse.ArgumentParser(description="NeuroPLC Python Cortex")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7000)
    parser.add_argument("--attack-mode", action="store_true")
    parser.add_argument("--model", type=str, default="models/neuro_v1.onnx", help="Path to ONNX model")
    args = parser.parse_args()

    run(args.host, args.port, args.attack_mode, args.model)


if __name__ == "__main__":
    main()
