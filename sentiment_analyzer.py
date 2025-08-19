# -*- coding: utf-8 -*-
"""
sentiment_analyzer.py
- Agrega sentimento de News + Twitter
- Compatível com: get_sentiment_for_symbol(symbol, use_news=True, use_twitter=True, last_price=None)
- Retorna sempre: {"score": 0..1, "news_n": int, "tw_n": int}

Observações:
- Se módulos específicos de news/twitter não estiverem presentes, devolve neutro (0.5) e contagem 0.
- Pesos lidos das envs (aceita vazio sem quebrar):
    WEIGHT_NEWS     (default 1.0)
    WEIGHT_TWITTER  (default 1.0)
"""

import os
from typing import Tuple, Dict, Any

# --------- util ---------
def _fenv(name: str, default: float) -> float:
    v = os.getenv(name, "")
    try:
        return float(v) if str(v).strip() != "" else float(default)
    except Exception:
        return float(default)

def _clip01(x: float) -> float:
    if x < 0.0: return 0.0
    if x > 1.0: return 1.0
    return x

# --------- News adapter (tenta várias assinaturas conhecidas) ---------
def _news_sent(symbol: str) -> Tuple[float, int]:
    """
    Retorna (score_news, n_news) com score em 0..1
    Caso não exista módulo/func ou erro -> (0.5, 0)
    """
    try:
        try:
            import news_fetcher as nf
        except Exception:
            nf = None

        if nf is None:
            return (0.5, 0)

        # Tenta funções por ordem
        candidates = [
            "get_sentiment_for_symbol",
            "get_news_sentiment",
            "fetch_news_sentiment",
            "news_sentiment_for",
        ]
        for fn in candidates:
            if hasattr(nf, fn):
                res = getattr(nf, fn)(symbol)
                # Normaliza o retorno
                if isinstance(res, dict):
                    s = res.get("score", res.get("value", 0.5))
                    n = res.get("count", res.get("n", res.get("news_n", 0)))
                    try: s = float(s)
                    except Exception: s = 0.5
                    if s > 1.0: s /= 100.0
                    return (_clip01(s), int(n) if n is not None else 0)
                elif isinstance(res, (tuple, list)):
                    s = 0.5
                    n = 0
                    if len(res) >= 1:
                        try: s = float(res[0])
                        except Exception: s = 0.5
                    if len(res) >= 2:
                        try: n = int(res[1])
                        except Exception: n = 0
                    if s > 1.0: s /= 100.0
                    return (_clip01(s), n)
                else:
                    try:
                        s = float(res)
                        if s > 1.0: s /= 100.0
                        return (_clip01(s), 0)
                    except Exception:
                        return (0.5, 0)
        # Nenhuma função conhecida
        return (0.5, 0)
    except Exception:
        return (0.5, 0)

# --------- Twitter adapter (tenta várias assinaturas conhecidas) ---------
def _twitter_sent(symbol: str) -> Tuple[float, int]:
    """
    Retorna (score_tw, n_tweets) com score em 0..1
    Caso não exista módulo/func ou erro -> (0.5, 0)
    """
    try:
        try:
            import twitter_fetcher as tf
        except Exception:
            tf = None

        if tf is None:
            return (0.5, 0)

        candidates = [
            "get_sentiment_for_symbol",
            "get_twitter_sentiment",
            "fetch_twitter_sentiment",
            "twitter_sentiment_for",
        ]
        for fn in candidates:
            if hasattr(tf, fn):
                res = getattr(tf, fn)(symbol)
                if isinstance(res, dict):
                    s = res.get("score", res.get("value", 0.5))
                    n = res.get("count", res.get("n", res.get("tw_n", 0)))
                    try: s = float(s)
                    except Exception: s = 0.5
                    if s > 1.0: s /= 100.0
                    return (_clip01(s), int(n) if n is not None else 0)
                elif isinstance(res, (tuple, list)):
                    s = 0.5
                    n = 0
                    if len(res) >= 1:
                        try: s = float(res[0])
                        except Exception: s = 0.5
                    if len(res) >= 2:
                        try: n = int(res[1])
                        except Exception: n = 0
                    if s > 1.0: s /= 100.0
                    return (_clip01(s), n)
                else:
                    try:
                        s = float(res)
                        if s > 1.0: s /= 100.0
                        return (_clip01(s), 0)
                    except Exception:
                        return (0.5, 0)
        return (0.5, 0)
    except Exception:
        return (0.5, 0)

# --------- API principal (usada pelo main) ---------
def get_sentiment_for_symbol(symbol: str,
                             use_news: bool = True,
                             use_twitter: bool = True,
                             last_price: float = None,   # <- aceita, mas é opcional/ignorado
                             ) -> Dict[str, Any]:
    """
    Retorna SEMPRE um dict: {"score": 0..1, "news_n": int, "tw_n": int}
    """
    # Pesos (envs tolerantes a empty string)
    w_news = _fenv("WEIGHT_NEWS", 1.0)
    w_tw   = _fenv("WEIGHT_TWITTER", 1.0)

    s_news, n_news = _news_sent(symbol) if use_news else (0.5, 0)
    s_tw,   n_tw   = _twitter_sent(symbol) if use_twitter else (0.5, 0)

    denom = (w_news + w_tw) if (w_news + w_tw) > 0 else 1.0
    mix = (s_news * w_news + s_tw * w_tw) / denom

    return {
        "score": _clip01(mix),
        "news_n": int(n_news),
        "tw_n": int(n_tw),
    }
