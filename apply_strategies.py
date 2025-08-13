# -*- coding: utf-8 -*-
import time
from statistics import fmean
from indicators import rsi, macd, ema, bollinger
from indicators_extra import ichimoku, parabolic_sar, stochastic, vwap, obv
from config import (
    MIN_CONFIDENCE,
    USE_TECH_EXTRA, USE_VOLUME_INDICATORS,
    TECH_W_ICHI, TECH_W_SAR, TECH_W_STOCH,
    TECH_W_VWAP, TECH_W_OBV
)

def score_signal(closes, highs=None, lows=None, volumes=None):
    if len(closes) < 60:
        return None

    # ----- Core (existente) -----
    r = rsi(closes, 14)
    macd_line, signal_line, hist = macd(closes, 12, 26, 9)
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    bb_up, bb_mid, bb_low = bollinger(closes, 20, 2.0)

    i = len(closes) - 1
    c = closes[i]

    is_rsi_bull   = 45 <= (r[i] or 0) <= 65 if r[i] is not None else False
    is_macd_cross = (
        macd_line[i] is not None and signal_line[i] is not None and
        macd_line[i-1] is not None and signal_line[i-1] is not None and
        macd_line[i] > signal_line[i] and macd_line[i-1] <= signal_line[i-1]
    )
    is_trend_up   = (ema20[i] is not None and ema50[i] is not None and ema20[i] > ema50[i])
    near_bb_low   = (bb_low[i] is not None) and (c <= bb_low[i]*1.01)

    s_rsi   = 1.0 if is_rsi_bull else (0.6 if (r[i] is not None and 40 <= r[i] <= 70) else 0.0)
    s_macd  = 1.0 if is_macd_cross else (0.7 if (hist[i] or 0) > 0 else 0.2)
    s_trend = 1.0 if is_trend_up else 0.3
    s_bb    = 1.0 if near_bb_low else 0.5

    scores = [s_rsi, s_macd, s_trend, s_bb]

    # ----- Extras sem volume (ativados por flag) -----
    if USE_TECH_EXTRA and highs is not None and lows is not None:
        conv, base, span_a, span_b = ichimoku(highs, lows)
        psar = parabolic_sar(highs, lows)
        K, D = stochastic(highs, lows, closes)

        i_ok = i < len(span_a) and i < len(span_b) and i < len(conv) and i < len(base)
        ichi_bull = (i_ok and span_a[i] is not None and span_b[i] is not None and conv[i] is not None and base[i] is not None
                     and c > span_a[i] > span_b[i] and conv[i] >= base[i])

        sar_bull  = (psar[i] is not None and c > psar[i] and (i>0 and (psar[i-1] is None or c <= psar[i-1])==False))

        st_cross = (K[i] is not None and D[i] is not None and i>0 and K[i] > D[i] and (K[i-1] or 0) <= (D[i-1] or 0))
        st_zone  = (K[i] is not None and 20 <= K[i] <= 80)

        s_ichi  = (1.0 if ichi_bull else 0.6) * TECH_W_ICHI
        s_sar   = (1.0 if sar_bull  else 0.5) * TECH_W_SAR
        s_stoch = (1.0 if (st_cross and st_zone) else (0.6 if st_zone else 0.3)) * TECH_W_STOCH
        scores.extend([s_ichi, s_sar, s_stoch])

        # ----- Com volume (só se houver volumes E flag ligada) -----
        if USE_VOLUME_INDICATORS and volumes is not None:
            vwap_arr = vwap(highs, lows, closes, volumes)
            obv_arr  = obv(closes, volumes)
            s_vwap = 1.0 if (vwap_arr[i] is not None and c >= vwap_arr[i]) else 0.5
            s_obv  = 1.0 if (obv_arr[i]  is not None and i>0 and obv_arr[i] > obv_arr[i-1]) else 0.5
            scores.extend([s_vwap * TECH_W_VWAP, s_obv * TECH_W_OBV])

    # agregação + leve normalização por hist (proxy volatilidade)
    score = fmean(scores)
    recent = [abs(h) for h in hist[-20:] if h is not None]
    if recent:
        vol_boost = min(max(abs(hist[i]) / (max(recent) + 1e-9), 0.0), 1.0)
        score = 0.85*score + 0.15*vol_boost
    return max(0.0, min(1.0, score))

def build_trade_plan(closes, highs=None, lows=None, risk_ratio_tp=2.0, risk_ratio_sl=1.0):
    if len(closes) < 30:
        return None
    import statistics
    last = closes[-1]
    diffs = [abs(closes[j] - closes[j-1]) for j in range(-15, 0)]
    atr_like = statistics.fmean(diffs)
    sl = last - (atr_like * risk_ratio_sl)
    tp = last + (atr_like * risk_ratio_tp)
    return {"entry": last, "tp": tp, "sl": sl}

def generate_signal(symbol, candles):
    closes = [c["close"] for c in candles]
    highs  = [c.get("high") for c in candles]
    lows   = [c.get("low")  for c in candles]
    volumes= [c.get("volume") for c in candles] if "volume" in candles[0] else None

    score = score_signal(closes, highs, lows, volumes)
    if score is None:
        return None

    plan = build_trade_plan(closes, highs, lows)
    if plan is None:
        return None

    sig = {
        "symbol": symbol,
        "timestamp": int(time.time()),
        "confidence": round(score, 4),
        "entry": plan["entry"],
        "tp": plan["tp"],
        "sl": plan["sl"],
        "strategy": "RSI+MACD+EMA+BB" + ("+EXTRA" if USE_TECH_EXTRA else "") + ("+VOL" if USE_VOLUME_INDICATORS else ""),
        "source": "coingecko"
    }
    thr = MIN_CONFIDENCE if MIN_CONFIDENCE<=1 else MIN_CONFIDENCE/100.0
    if sig["confidence"] < thr:
        return None
    return sig
