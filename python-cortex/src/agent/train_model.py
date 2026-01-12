import argparse
import numpy as np
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from skl2onnx import convert_sklearn
from skl2onnx.common.data_types import FloatTensorType
import onnx
from pathlib import Path

def train_and_export(output_path: str):
    print("Training NeuroPLC demo model...")
    
    # Generate synthetic training data
    # Input features: [speed_rpm, temp_c, pressure_bar, speed_trend, temp_trend]
    # Optimal logic: 
    # - If temp is low, run fast
    # - If temp is high, slow down
    # - If pressure is high, slow down slightly
    
    n_samples = 1000
    rng = np.random.RandomState(42)
    
    X = rng.rand(n_samples, 5)
    # Scale to realistic ranges
    # 0: speed (0-3000)
    # 1: temp (20-90)
    # 2: pressure (0-10)
    # 3: speed_trend (-50 to 50)
    # 4: temp_trend (-2 to 2)
    X[:, 0] = X[:, 0] * 3000
    X[:, 1] = X[:, 1] * 70 + 20
    X[:, 2] = X[:, 2] * 10
    X[:, 3] = (X[:, 3] - 0.5) * 100
    X[:, 4] = (X[:, 4] - 0.5) * 4
    
    # Target: Optimal speed
    # Base is 2500
    # Reduce by 50 for every degree over 50C
    # Reduce by 100 for every bar over 5
    y_target = np.full(n_samples, 2500.0)
    
    over_temp = np.maximum(0, X[:, 1] - 50)
    over_pressure = np.maximum(0, X[:, 2] - 5)
    
    y_target -= over_temp * 50
    y_target -= over_pressure * 100
    
    # Add noise & confidence training
    # For confidence, we'll just output a static 0.95 for this demo regressor, 
    # but in reality it would be a separate head.
    # Here we'll train a multi-output regressor: [target_speed, confidence]
    
    # Confidence drops if temp is very high (>80) or pressure very high (>8)
    confidence = np.full(n_samples, 0.95)
    mask_unsafe = (X[:, 1] > 80) | (X[:, 2] > 8)
    confidence[mask_unsafe] = 0.6
    
    y = np.column_stack((y_target, confidence))
    
    # Train model
    # Use Random Forest for non-linearity
    model = Pipeline([
        ('scaler', StandardScaler()),
        ('regressor', RandomForestRegressor(n_estimators=10, max_depth=5, random_state=42))
    ])
    
    model.fit(X, y)
    print("Model trained. Score:", model.score(X, y))
    
    # Export to ONNX
    # Input type must match the model's input (float32, 5 features)
    initial_type = [('float_input', FloatTensorType([None, 5]))]
    onnx_model = convert_sklearn(model, initial_types=initial_type)
    
    # Save
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(onnx_model.SerializeToString())
        
    print(f"Model saved to {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=str, default="models/neuro_v1.onnx")
    args = parser.parse_args()
    
    train_and_export(args.output)
