# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py
Unifica sentimento de News + Twitter e SEMPRE devolve um dict:
{
  "score": float 0..1,
  "parts": {"news": float, "twitter": float},
  "counts": {"news": int, "twitter": int}
}
Compatível com projetos onde as funções de fetch possam não existir.
"""

from __future__ import annotations
import os
from typing import Tuple, Dict, Any

# ------------------------------------------------------------
# Helpers de leitura de env
# ------------------------------------------------------------
def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "y", "on")

def _f(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

# Pesos (podem vir do .env / variables do Railway)
WEIGHT_NEWS = _f("WEIGHT_NEWS", _f("NEWS_WEIGHT", 1.0))
WEIGHT_TW   = _f("WEIGHT_TW",   _f("TWITTER_WEIGHT", 1.0))
# Se não setar nada, a mistura vai virar a média simples
if WEIGHT_NEWS == 0 and WEIGHT_TW == 0:
    WEIGHT_NEWS = 1.0
    WEIGHT_TW   = 1.0

USE_NEWS = _bool("NEWS_USE", True) or _bool("ENABLE_NEWS", True)
USE_TW   = _bool("TWITTER_USE", False)

# ------------------------------------------------------------
# Importa fontes se existirem
# news_fetcher: precisa expor get_sentiment(symbol) -> (score 0..1, total_n)
# twitter_fetcher: idem
# ------------------------------------------------------------
_news_ok = False
_tw_ok   = False
try:
    # seu módulo existente
    from news_fetcher import get_sentiment as _news_get
    _news_ok = True
except Exception:
    _news_ok = False

try:
    # se você tem outro nome, ajuste aqui
    from twitter_fetcher import get_sentiment as _tw_get
    _tw_ok = True
except Exception:
    _tw_ok = False

# ------------------------------------------------------------
# Funções internas seguras
# ------------------------------------------------------------
def _safe_news(symbol: str) -> Tuple[float, int]:
    """Retorna (score 0..1, n). Se indisponível, neutro 0.5 / n=0."""
    if not USE_NEWS or not _news_ok:
        return (0.5, 0)
    try:
        res = _news_get(symbol)
        # Aceita formatos variados
        if isinstance(res, tuple) and len(res) >= 1:
            score = float(res[0])
            n = int(res[1]) if len(res) > 1 else 0
        elif isinstance(res, dict):
            score = float(res.get("score", 0.5))
            n = int(res.get("n", 0))
        else:
            score = float(res)
            n = 0
        # normaliza
        if score > 1.0:
            score /= 100.0
        score = max(0.0, min(1.0, score))
        return (score, n)
    except Exception:
        return (0.5, 0)

def _safe_twitter(symbol: str) -> Tuple[float, int]:
    """Retorna (score 0..1, n). Se indisponível, neutro 0.5 / n=0."""
    if not USE_TW or not _tw_ok:
        return (0.5, 0)
    try:
        res = _tw_get(symbol)
        if isinstance(res, tuple) and len(res) >= 1:
            score = float(res[0])
            n = int(res[1]) if len(res) > 1 else 0
        elif isinstance(res, dict):
            score = float(res.get("score", 0.5))
            n = int(res.get("n", 0))
        else:
            score = float(res)
            n = 0
        if score > 1.0:
            score /= 100.0
        score = max(0.0, min(1.0, score))
        return (score, n)
    except Exception:
        return (0.5, 0)

# ------------------------------------------------------------
# API pública
# ------------------------------------------------------------
def get_sentiment_for_symbol(symbol: str) -> Dict[str, Any]:
    """
    Mistura News + Twitter com os pesos configurados.
    SEMPRE retorna dict para o main não quebrar.
    """
    news_score, news_n = _safe_news(symbol)
    tw_score,   tw_n   = _safe_twitter(symbol)

    # mistura ponderada
    w_sum = WEIGHT_NEWS + WEIGHT_TW
    if w_sum <= 0:
        mix = 0.5
    else:
        mix = (news_score * WEIGHT_NEWS + tw_score * WEIGHT_TW) / w_sum

    # bound
    mix = max(0.0, min(1.0, mix))

    return {
        "score": mix,
        "parts": {"news": news_score, "twitter": tw_score},
        "counts": {"news": news_n, "twitter": tw_n},
        "enabled": {"news": USE_NEWS and _news_ok, "twitter": USE_TW and _tw_ok},
        "weights": {"news": WEIGHT_NEWS, "twitter": WEIGHT_TW},
    }
