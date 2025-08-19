# -*- coding: utf-8 -*-
"""
apply_strategies.py — cálculo de score técnico (robusto)
- score_signal(ohlc) NUNCA lança exceção; retorna [0..1]
- generate_signal(ohlc) cria tp/sl básicos quando score alto
"""

from typing import List, Dict, Tuple

# ====== Helpers/indicadores do seu projeto ======
# Se você já tem um _score_from_indicators em outro arquivo,
# pode manter — aqui tem uma implementação simples e segura.
try:
    from indicators import rsi, macd_line, ema
except Exception:
    # Fallbacks triviais pra evitar import error em runtime
    def rsi(closes, period=14):
        if not closes or len(closes) < period + 1: return 50.0
        gains = []
        losses = []
        for i in range(1, len(closes)):
            d = closes[i] - closes[i-1]
            gains.append(max(d, 0.0))
            losses.append(max(-d, 0.0))
        avg_gain = sum(gains[-period:]) / period if period <= len(gains) else 0.0
        avg_loss = sum(losses[-period:]) / period if period <= len(losses) else 1e-9
        rs = avg_gain / avg_loss if avg_loss > 0 else 0.0
        return 100.0 - (100.0 / (1.0 + rs))

    def ema(closes, period=20):
        if not closes: return 0.0
        k = 2.0 / (period + 1.0)
        v = closes[0]
        for c in closes[1:]:
            v = c * k + v * (1.0 - k)
        return v

    def macd_line(closes, fast=12, slow=26, signal=9):
        if not closes: return (0.0, 0.0, 0.0)
        import math
        def _ema(vals, p):
            k = 2.0 / (p + 1.0)
            v = vals[0]
            for x in vals[1:]:
                v = x * k + v * (1.0 - k)
            return v
        macd = _ema(closes, fast) - _ema(closes, slow)
        sig  = _ema([macd]*signal, signal)  # simplificado p/ robustez
        hist = macd - sig
        return (macd, sig, hist)

# =================================================

def _score_from_indicators(ohlc: List[Dict]) -> float:
    """
    Score simples combinando RSI & MACD & EMA — retorna [0..1]
    """
    try:
        closes = [b["c"] for b in ohlc if isinstance(b, dict) and "c" in b]
        if len(closes) < 30:
            return 0.0
        r = rsi(closes, 14)              # 0..100
        m, s, h = macd_line(closes)      # pode ser negativo
        e20 = ema(closes, 20)
        e50 = ema(closes, 50)

        # Normalizações toscas mas seguras
        r_norm = r / 100.0
        macd_norm = 0.5 + max(-1.0, min(1.0, m * 0.001))
        ema_norm = 0.5 + max(-1.0, min(1.0, (e20 - e50) / (abs(e50) + 1e-9))) * 0.5

        s = 0.4 * r_norm + 0.3 * macd_norm + 0.3 * ema_norm
        if s < 0.0: s = 0.0
        if s > 1.0: s = 1.0
        return s
    except Exception:
        return 0.0

def score_signal(ohlc: List[Dict]) -> float:
    """
    Retorna um score técnico [0..1].
    NUNCA lança exceção (em erro => 0.0).
    Aceita valores percentuais (>1) e dicionários/tuplas.
    """
    try:
        s = _score_from_indicators(ohlc)
        if isinstance(s, dict):
            s = s.get("score", s.get("value", 0.0))
        elif isinstance(s, tuple):
            s = s[0]
        s = float(s if s is not None else 0.0)
        if s > 1.0:  # caso seja em %
            s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception:
        return 0.0

def generate_signal(ohlc: List[Dict], risk=0.01, reward_mult=2.0) -> Dict:
    """
    Gera um sinal simples com entry/TP/SL a partir do último close.
    """
    if not ohlc:
        return {}
    try:
        c = float(ohlc[-1]["c"])
        sl = c * (1.0 - risk)
        tp = c * (1.0 + risk * reward_mult)
        return {"entry": c, "sl": sl, "tp": tp}
    except Exception:
        return {}
