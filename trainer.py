# -*- coding: utf-8 -*-
"""
trainer.py
- Monta dataset a partir do history_manager
- Treina modelo linear (SGD) e salva em model.json
"""

import os
from typing import List
from history_manager import get_training_dataset
from model_manager import sgd_train, save_model

TRAIN_MIN_SAMPLES = int(os.getenv("TRAIN_MIN_SAMPLES", "200"))

def train_and_save():
    X, y, feat_names = get_training_dataset(min_samples=TRAIN_MIN_SAMPLES)
    if not X:
        print(f"🟡 trainer: amostras insuficientes (<{TRAIN_MIN_SAMPLES}).")
        return False
    print(f"📚 trainer: dataset X={len(X)}x{len(X[0])} | positivos={sum(y)} | negativos={len(y)-sum(y)}")
    w, b = sgd_train(X, y, lr=0.01, epochs=12)
    save_model(w, b, feat_names)
    print("✅ trainer: modelo salvo em model.json")
    return True

if __name__ == "__main__":
    train_and_save()
