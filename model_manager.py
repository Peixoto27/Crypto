# -*- coding: utf-8 -*-
"""
model_manager.py
- Modelo linear leve (SGD) implementado “na unha” (sem dependências).
- Salva/carrega pesos em JSON.
"""

import os
import json
from typing import List, Tuple

MODEL_FILE = os.getenv("MODEL_FILE", "model.json")

def save_model(weights, bias, feature_names: List[str]) -> None:
    data = {"weights": weights, "bias": bias, "features": feature_names}
    with open(MODEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_model() -> Tuple[List[float], float, List[str]]:
    try:
        with open(MODEL_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d["weights"], d["bias"], d.get("features", [])
    except Exception:
        return [], 0.0, []

def _sigmoid(z: float) -> float:
    if z > 30:  # evita overflow
        return 1.0
    if z < -30:
        return 0.0
    import math
    return 1.0 / (1.0 + math.exp(-z))

def sgd_train(X: List[List[float]], y: List[int], lr: float = 0.01, epochs: int = 10):
    if not X:
        return [], 0.0
    n_feat = len(X[0])
    w = [0.0] * n_feat
    b = 0.0

    for _ in range(epochs):
        for xi, yi in zip(X, y):
            z = sum(wj * xj for wj, xj in zip(w, xi)) + b
            p = _sigmoid(z)
            # gradiente binária logística
            err = (p - yi)
            for j in range(n_feat):
                w[j] -= lr * err * xi[j]
            b -= lr * err
    return w, b

def predict_proba(vec: List[float], weights: List[float], bias: float) -> float:
    z = sum(wj * xj for wj, xj in zip(weights, vec)) + bias
    return _sigmoid(z)
