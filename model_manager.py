# -*- coding: utf-8 -*-
"""
model_manager.py — utilitário para carregar/salvar modelo e prever com segurança.

- Lê/salva o caminho de modelo de MODEL_FILE (env; default: model.pkl)
- Lê metadados em MODEL_FILE_meta.json (lista de features usadas no treino)
- Expõe: load_model(), predict_proba(features_dict), has_model()
"""

import os
import json
from typing import Dict, Optional, Tuple, Any
import numpy as np

from joblib import load, dump

MODEL_FILE = os.getenv("MODEL_FILE", "model.pkl")

def _meta_path() -> str:
    base, _ = os.path.splitext(MODEL_FILE)
    return base + "_meta.json"

_cached: Tuple[Optional[object], Optional[dict]] = (None, None)
# (_model, _meta)

def has_model() -> bool:
    # considera presença do arquivo do modelo; meta é opcional
    return os.path.exists(MODEL_FILE)

def _extract_meta_from_obj(obj: Any) -> Optional[dict]:
    """
    Tenta extrair um dicionário de metadados (com chave "features") de
    várias estruturas de objeto que podem ser salvas no repositório.
    """
    # caso obj seja dict salvo como {"model": ..., "meta": {...}} ou {"model":..., "features": [...]}
    if isinstance(obj, dict):
        if "meta" in obj and isinstance(obj["meta"], dict):
            return obj["meta"]
        if "features" in obj:
            return {"features": obj["features"]}

    # wrappers/objetos que possam ter atributos com nomes comuns
    for attr in ("meta", "features", "feat_cols", "feature_names", "columns"):
        try:
            if hasattr(obj, attr):
                val = getattr(obj, attr)
                if isinstance(val, (list, tuple)):
                    return {"features": list(val)}
                if isinstance(val, dict) and "features" in val:
                    return val
        except Exception:
            # evitar falha ao inspecionar objetos inesperados
            continue

    return None

def load_model(force: bool = False) -> Tuple[Optional[object], Optional[dict]]:
    """Carrega modelo + metadados. Cacheado em memória."""
    global _cached
    if not force and _cached[0] is not None and _cached[1] is not None:
        return _cached

    if not has_model():
        _cached = (None, None)
        return _cached

    try:
        loaded = load(MODEL_FILE)
    except Exception:
        _cached = (None, None)
        return _cached

    mdl = None
    meta = None

    # Caso comum: joblib.dump({"model": model, "meta": {...}}, MODEL_FILE)
    if isinstance(loaded, dict) and "model" in loaded:
        mdl = loaded.get("model")
        meta = loaded.get("meta") or ({"features": loaded.get("features")} if "features" in loaded else None)
    else:
        # caso carregado seja o próprio estimador / pipeline / wrapper
        mdl = loaded
        # primeiro: tentar ler arquivo de meta separado
        try:
            if os.path.exists(_meta_path()):
                with open(_meta_path(), "r", encoding="utf-8") as f:
                    meta = json.load(f)
        except Exception:
            meta = None

        # se não houver meta em arquivo, tentar extrair do objeto carregado
        if meta is None:
            try:
                meta = _extract_meta_from_obj(mdl)
            except Exception:
                meta = None

    _cached = (mdl, meta)
    return _cached

def save_model(model, meta: dict) -> bool:
    """Salvar modelo + metadados (opcionalmente usado pelo trainer)."""
    try:
        dump(model, MODEL_FILE)
        # salva metadados se fornecido
        try:
            with open(_meta_path(), "w", encoding="utf-8") as f:
                json.dump(meta or {}, f, ensure_ascii=False, indent=2)
        except Exception:
            # não falhar se não conseguir salvar meta
            pass
        # invalida cache e recarrega
        load_model(force=True)
        return True
    except Exception:
        return False

def predict_proba(features: Dict[str, float]) -> Optional[float]:
    """Recebe um dicionário de features, alinha às features do treino,
    preenche ausentes com 0.0 e retorna probabilidade da classe positiva (0..1)."""
    mdl, meta = load_model()
    if mdl is None:
        return None

    feats = None
    if meta and isinstance(meta, dict):
        feats = meta.get("features")

    # se ainda não temos features, tentar extrair do modelo carregado (última tentativa)
    if not feats:
        extracted = _extract_meta_from_obj(mdl)
        if extracted:
            feats = extracted.get("features")

    if not feats:
        # sem lista de features não conseguimos alinhar o dicionário -> retorna None
        return None

    row = [float(features.get(k, 0.0)) for k in feats]
    try:
        proba = mdl.predict_proba(np.array([row]))[:, 1][0]
        # robustez numérica
        proba = max(0.0, min(1.0, float(proba)))
        return proba
    except Exception:
        return None
