import argparse
import json
import os
import socket
import time
import hashlib
from typing import Optional

from digital_twin import BasyxAdapter, BasyxConfig
from pathlib import Path
try:
    from agent.ml_inference import MLRecommendationEngine, SafetyBoundedRecommender
except ImportError:
    print("Warning: ML modules not found. Running in rule-based mode.")
    MLRecommendationEngine = None
    SafetyBoundedRecommender = None

def compute_recommendation(
    state: dict, 
    attack_mode: bool, 
    cycle: int,
    recommender: Optional[object] = None
) -> tuple[Optional[float], float, str, dict]:
    speed = float(state.get("motor_speed_rpm", 0.0))
    temp = float(state.get("motor_temp_c", 0.0))
    pressure = float(state.get("pressure_bar", 0.0))
    
    # Track history (mock for now, in prod use a sliding window buffer)
    speed_hist = [speed] 
    temp_hist = [temp]

    if attack_mode and cycle % 10 == 0:
        target = 5000.0
        confidence = 0.2
        analysis = "attack_mode: requesting unsafe speed to test firewall"
        envelope = {
            "analysis": analysis,
            "target_speed_rpm": target,
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

    # Common envelope fields
    envelope["state"] = {
        "motor_speed_rpm": speed,
        "motor_temp_c": temp,
        "pressure_bar": pressure,
        "timestamp_us": int(state.get("timestamp_us", 0)),
    }
    
    return target, confidence, analysis, envelope


def hash_envelope(envelope: dict) -> str:
    encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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

    while True:
        try:
            with socket.create_connection(addr, timeout=5) as sock:
                sock.settimeout(1.0)
                file = sock.makefile("rwb")
                print(f"Connected to spine at {host}:{port}")
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
                    if basyx_adapter and not basyx_ready:
                        try:
                            basyx_adapter.ensure_models()
                            basyx_ready = True
                            print("BaSyx: AAS and submodels initialized")
                        except OSError as exc:
                            print(f"BaSyx init failed: {exc}")

                    target, confidence, analysis, envelope = compute_recommendation(
                        state, attack_mode, cycle, recommender
                    )
                    reasoning_hash = hash_envelope(envelope)

                    msg = {
                        "type": "recommendation",
                        "target_speed_rpm": target,
                        "confidence": confidence,
                        "reasoning_hash": reasoning_hash,
                        "client_unix_us": int(time.time() * 1_000_000),
                    }
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
                            if target is not None:
                                basyx_adapter.update_recommendation(
                                    target_speed=target,
                                    confidence=confidence,
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
