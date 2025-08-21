# -*- coding: utf-8 -*-
"""
model_manager.py — utilitário para carregar/salvar modelo e prever com segurança.

- Lê/salva o caminho de modelo de MODEL_FILE (env; default: model.pkl)
- Lê metadados em MODEL_FILE_meta.json (lista de features usadas no treino)
- Expõe: load_model(), predict_proba(features_dict), has_model()
"""

import os
import json
from typing import Dict, Optional, Tuple
import numpy as np

from joblib import load, dump

MODEL_FILE = os.getenv("MODEL_FILE", "model.pkl")

def _meta_path() -> str:
    base, _ = os.path.splitext(MODEL_FILE)
    return base + "_meta.json"

_cached: Tuple[Optional[object], Optional[dict]] = (None, None)
# (_model, _meta)

def has_model() -> bool:
    return os.path.exists(MODEL_FILE) and os.path.exists(_meta_path())

def load_model(force: bool = False) -> Tuple[Optional[object], Optional[dict]]:
    """Carrega modelo + metadados. Cacheado em memória."""
    global _cached
    if not force and _cached[0] is not None and _cached[1] is not None:
        return _cached

    if not has_model():
        _cached = (None, None)
        return _cached

    try:
        mdl = load(MODEL_FILE)
    except Exception:
        _cached = (None, None)
        return _cached

    try:
        with open(_meta_path(), "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception:
        meta = None

    _cached = (mdl, meta)
    return _cached

def save_model(model, meta: dict) -> bool:
    """Salvar modelo + metadados (opcionalmente usado pelo trainer)."""
    try:
        dump(model, MODEL_FILE)
        with open(_meta_path(), "w", encoding="utf-8") as f:
            json.dump(meta or {}, f, ensure_ascii=False, indent=2)
        # invalida cache e recarrega
        load_model(force=True)
        return True
    except Exception:
        return False

def predict_proba(features: Dict[str, float]) -> Optional[float]:
    """
    Recebe um dicionário de features, alinha às features do treino,
    preenche ausentes com 0.0 e retorna probabilidade da classe positiva (0..1).
    """
    mdl, meta = load_model()
    if mdl is None or meta is None:
        return None

    feats = meta.get("features")
    if not feats:
        return None

    row = [float(features.get(k, 0.0)) for k in feats]
    try:
        proba = mdl.predict_proba(np.array([row]))[:, 1][0]
        # robustez numérica
        proba = max(0.0, min(1.0, float(proba)))
        return proba
    except Exception:
        return None
