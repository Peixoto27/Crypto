# -*- coding: utf-8 -*-
"""
signal_generator.py — motor de sinal técnico
- RSI + MACD + EMA20/EMA50 + Bollinger
- Plano de trade (entry/tp/sl) baseado em volatilidade recente
- Extras passivos (Stochastic, Ichimoku, PSAR) para debug (não afetam o score)

Espera candles no formato:
[{"open": float, "high": float, "low": float, "close": float, "ts": int}, ...]
"""

import os
import statistics
from statistics import fmean
from typing import Dict, List, Optional, Tuple

from indicators import rsi, macd, ema, bollinger  # já existentes no seu projeto
# extras passivos (arquivo indicators_extra.py)
try:
    from indicators_extra import stochastic_kd, ichimoku, parabolic_sar  # opcional
except Exception:
    stochastic_kd = ichimoku = parabolic_sar = None

# =========================
# Parâmetros por ENV
# =========================
# limite mínimo para considerar score (o corte REAL é feito no main via MIN_CONFIDENCE/SCORE_THRESHOLD)
MIN_LOCAL_SCORE = float(os.getenv("MIN_LOCAL_SCORE", "0.0"))

# pesos internos do score técnico (somam ~1.0)
W_RSI   = float(os.getenv("W_RSI",   "0.25"))
W_MACD  = float(os.getenv("W_MACD",  "0.30"))
W_TREND = float(os.getenv("W_TREND", "0.25"))
W_BB    = float(os.getenv("W_BB",    "0.20"))

# plano de trade
RISK_R  = float(os.getenv("RISK_R", "2.0"))  # relação TP:SL ~2:1
ATR_WIN = int(os.getenv("ATR_WIN", "15"))    # janela do "ATR-like"

# extras/log
EXTRA_LOG = os.getenv("EXTRA_INDICATORS_LOG", "0") == "1"
EXTRA_W   = float(os.getenv("EXTRA_SCORE_WEIGHT", "0.0"))  # mantenha 0.0 (não somar ao score)

# =========================
# Helpers
# =========================
def _last(values: List[Optional[float]]) -> Optional[float]:
    return None if not values else values[-1]

def _atr_like(closes: List[float], win: int) -> float:
    diffs = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
    recent = diffs[-win:] if len(diffs) >= win else diffs
    return statistics.fmean(recent) if recent else 0.0

def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

# =========================
# Score técnico
# =========================
def _score_components(closes: List[float]) -> Tuple[float, Dict[str, float], Dict[str, float]]:
    """
    Retorna:
      score (0..1),
      subscores {"rsi":..,"macd":..,"trend":..,"bb":..},
      debug     {"r":..,"macd_hist":..,"ema20":..,"ema50":..,"bb_low":..,"close":..}
    """
    n = len(closes)
    if n < 60:
        return 0.0, {}, {}

    # indicadores base
    r_arr = rsi(closes, 14)
    macd_line, signal_line, hist = macd(closes, 12, 26, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    bb_up, bb_mid, bb_low = bollinger(closes, 20, 2.0)

    i  = n - 1
    c  = closes[i]
    r_ = r_arr[i] if r_arr[i] is not None else 50.0

    # heurísticas
    is_rsi_bull   = 45 <= r_ <= 65
    is_macd_cross = (macd_line[i] is not None and signal_line[i] is not None and
                     macd_line[i] > signal_line[i] and macd_line[i-1] <= signal_line[i-1])
    is_trend_up   = (ema20[i] is not None and ema50[i] is not None and ema20[i] > ema50[i])
    near_bb_low   = (bb_low[i] is not None and c <= bb_low[i] * 1.01)

    # subscores [0..1]
    s_rsi   = 1.0 if is_rsi_bull else (0.6 if 40 <= r_ <= 70 else 0.0)
    s_macd  = 1.0 if is_macd_cross else (0.7 if (hist[i] is not None and hist[i] > 0) else 0.2)
    s_trend = 1.0 if is_trend_up else 0.3
    s_bb    = 1.0 if near_bb_low else 0.5

    # média ponderada
    score = (W_RSI*s_rsi + W_MACD*s_macd + W_TREND*s_trend + W_BB*s_bb) / max(
        W_RSI + W_MACD + W_TREND + W_BB, 1e-9
    )

    # leve normalização por volatilidade do MACD
    recent_hist = [abs(h) for h in hist[-20:] if h is not None]
    if recent_hist:
        vol_boost = _clamp01(abs(hist[i]) / (max(recent_hist) + 1e-9))
        score = 0.85*score + 0.15*vol_boost

    subs = {"rsi": round(s_rsi, 4), "macd": round(s_macd, 4), "trend": round(s_trend, 4), "bb": round(s_bb, 4)}
    dbg  = {
        "r": round(r_, 2),
        "macd_hist": None if hist[i] is None else round(hist[i], 6),
        "ema20": None if ema20[i] is None else round(ema20[i], 6),
        "ema50": None if ema50[i] is None else round(ema50[i], 6),
        "bb_low": None if bb_low[i] is None else round(bb_low[i], 6),
        "close": round(c, 6),
    }

    return _clamp01(score), subs, dbg

# =========================
# Plano de trade
# =========================
def _build_trade_plan(closes: List[float], rr: float = RISK_R) -> Optional[Dict[str, float]]:
    if len(closes) < 30:
        return None
    last = closes[-1]
    atrl = _atr_like(closes, ATR_WIN)
    if atrl <= 0:
        return None
    sl = last - (atrl * 1.0)
    tp = last + (atrl * rr)
    return {"entry": last, "tp": tp, "sl": sl}

# =========================
# API principal
# =========================
def generate_signal(symbol: str, candles: List[Dict]) -> Optional[Dict]:
    """
    Retorna um dict de sinal:
      {
        symbol, timestamp, confidence, entry, tp, sl, strategy,
        debug: {...}, source: "coingecko"
      }
    ou None se não há condições mínimas.
    """
    if not candles or len(candles) < 60:
        return None

    closes = [float(c["close"]) for c in candles]
    highs  = [float(c["high"])  for c in candles]
    lows   = [float(c["low"])   for c in candles]

    # score técnico base
    tech_score, subs, dbg = _score_components(closes)
    if tech_score < MIN_LOCAL_SCORE:
        return None

    # extras passivos (apenas logs)
    extras = {}
    if EXTRA_LOG:
        try:
            if stochastic_kd:
                k, d = stochastic_kd(highs, lows, closes, 14, 3, 3)
                extras["stoch_k"] = None if _last(k) is None else round(_last(k), 2)
                extras["stoch_d"] = None if _last(d) is None else round(_last(d), 2)
            if ichimoku:
                tenkan, kijun, span_a, span_b = ichimoku(highs, lows)
                extras["ichimoku"] = {
                    "tenkan": None if _last(tenkan) is None else round(_last(tenkan), 6),
                    "kijun":  None if _last(kijun)  is None else round(_last(kijun), 6),
                    "span_a": None if _last(span_a) is None else round(_last(span_a), 6),
                    "span_b": None if _last(span_b) is None else round(_last(span_b), 6),
                }
            if parabolic_sar:
                psar = parabolic_sar(highs, lows)
                extras["psar"] = None if _last(psar) is None else round(_last(psar), 6)
        except Exception as e:
            extras["error"] = str(e)

    # (opcional) somar extras ao score — mantenha 0.0 para não afetar
    if EXTRA_W > 0.0 and extras:
        # Exemplo simples: se stoch_k > stoch_d e close acima de kijun -> +0.1
        extra_conf = 0.0
        try:
            st_k = extras.get("stoch_k")
            st_d = extras.get("stoch_d")
            kij  = extras.get("ichimoku", {}).get("kijun")
            c    = dbg.get("close")
            if st_k is not None and st_d is not None and kij is not None and c is not None:
                if st_k > st_d and c > kij:
                    extra_conf = 0.7
                else:
                    extra_conf = 0.3
        except Exception:
            extra_conf = 0.0
        tech_score = _clamp01((1.0 - EXTRA_W)*tech_score + EXTRA_W*extra_conf)

    plan = _build_trade_plan(closes)
    if plan is None:
        return None

    sig = {
        "symbol": symbol,
        "timestamp": int(candles[-1].get("ts", 0) or 0),
        "confidence": round(tech_score, 4),  # apenas técnico; o final com sentimento é feito no main
        "entry": plan["entry"],
        "tp": plan["tp"],
        "sl": plan["sl"],
        "risk_reward": RISK_R,
        "strategy": "RSI+MACD+EMA+BB",  # o main acrescenta +NEWS quando combina
        "source": "coingecko",
        "debug": {
            "subs": subs,
            "base": dbg,
            "extras": extras if extras else None,
        },
    }
    return sig
