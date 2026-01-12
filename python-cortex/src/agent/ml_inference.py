import onnxruntime as ort
import numpy as np
from pathlib import Path
from typing import Optional, Tuple
import hashlib
import json

class MLRecommendationEngine:
    """ONNX-based ML inference for speed recommendations."""
    
    def __init__(self, model_path: Path):
        self.session = ort.InferenceSession(
            str(model_path),
            providers=['CPUExecutionProvider']
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        
        # Model metadata for audit trail
        self.model_hash = self._hash_model(model_path)
        self.model_version = model_path.stem
    
    def _hash_model(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    
    def predict(
        self,
        speed_rpm: float,
        temp_c: float,
        pressure_bar: float,
        speed_history: list[float],  # Last N speed readings
        temp_history: list[float],
    ) -> Tuple[float, float, dict]:
        """
        Returns: (target_speed, confidence, audit_envelope)
        """
        # Prepare input tensor
        # Features: [current_speed, current_temp, pressure, speed_trend, temp_trend]
        speed_trend = np.mean(np.diff(speed_history)) if len(speed_history) > 1 else 0.0
        temp_trend = np.mean(np.diff(temp_history)) if len(temp_history) > 1 else 0.0
        
        input_array = np.array([[
            speed_rpm,
            temp_c,
            pressure_bar,
            speed_trend,
            temp_trend
        ]], dtype=np.float32)
        
        # Run inference
        outputs = self.session.run([self.output_name], {self.input_name: input_array})
        
        # Parse outputs: [target_speed, confidence]
        # Shape is (1, 2)
        target_speed = float(outputs[0][0, 0])
        confidence = float(np.clip(outputs[0][0, 1], 0.0, 1.0))
        
        # Build audit envelope
        envelope = {
            "model_version": self.model_version,
            "model_hash": self.model_hash,
            "input": {
                "speed_rpm": speed_rpm,
                "temp_c": temp_c,
                "pressure_bar": pressure_bar,
                "speed_trend": speed_trend,
                "temp_trend": temp_trend,
            },
            "output": {
                "target_speed": target_speed,
                "confidence": confidence,
            }
        }
        
        return target_speed, confidence, envelope


class SafetyBoundedRecommender:
    """Wraps ML engine with safety bounds checking."""
    
    def __init__(
        self,
        ml_engine: MLRecommendationEngine,
        max_speed: float = 3000.0,
        min_speed: float = 0.0,
        max_rate_of_change: float = 100.0,  # RPM per second
    ):
        self.ml = ml_engine
        self.max_speed = max_speed
        self.min_speed = min_speed
        self.max_rate = max_rate_of_change
        self.last_target: Optional[float] = None
    
    def recommend(
        self,
        speed_rpm: float,
        temp_c: float,
        pressure_bar: float,
        speed_history: list[float],
        temp_history: list[float],
        dt_s: float = 1.0,
    ) -> Tuple[float, float, dict]:
        target, confidence, envelope = self.ml.predict(
            speed_rpm, temp_c, pressure_bar, speed_history, temp_history
        )
        
        # Apply safety bounds here too (defense in depth)
        # Even though Rust spine has final say, Cortex should try to be safe
        
        # 1. Clamp absolute limits
        target = max(self.min_speed, min(self.max_speed, target))
        
        # 2. Rate limiting
        if self.last_target is not None:
            max_delta = self.max_rate * dt_s
            delta = target - self.last_target
            if abs(delta) > max_delta:
                target = self.last_target + (max_delta if delta > 0 else -max_delta)
                envelope["clamped_reason"] = "rate_limit"
        
        self.last_target = target
        return target, confidence, envelope
