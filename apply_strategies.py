# apply_strategies.py
import math
import os

# --- helpers robustos ---
def _as_float(x, default=0.0):
    try:
        if x is None:
            return default
        v = float(x)
        if math.isnan(v) or math.isinf(v):
            return default
        return v
    except Exception:
        return default

def _clip01(v):
    return max(0.0, min(1.0, v))

def _env_f(name, default):
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default

def _env_b(name, default=False):
    v = os.getenv(name, str(default)).lower()
    return v in ("1", "true", "yes", "on")

# pesos / toggles (compatíveis com seu ENV)
W_RSI      = _env_f("W_RSI",       _env_f("RSI_WEIGHT",       1.0))
W_MACD     = _env_f("W_MACD",      _env_f("MACD_WEIGHT",      1.0))
W_EMA      = _env_f("W_EMA",       _env_f("EMA_WEIGHT",       1.0))
W_BB       = _env_f("W_BB",        _env_f("BB_WEIGHT",        0.7))
W_STOCHRSI = _env_f("W_STOCHRSI",  _env_f("STOCHRSI_WEIGHT",  0.8))
W_ADX      = _env_f("W_ADX",       _env_f("ADX_WEIGHT",       0.8))
W_ATR      = _env_f("W_ATR",       _env_f("ATR_WEIGHT",       0.0))
W_CCI      = _env_f("W_CCI",       _env_f("CCI_WEIGHT",       0.5))

EN_STOCHRSI = _env_b("EN_STOCHRSI", True)
EN_ADX      = _env_b("EN_ADX",      True)
EN_ATR      = _env_b("EN_ATR",      False)
EN_CCI      = _env_b("EN_CCI",      True)

WEIGHT_TECH = _env_f("WEIGHT_TECH", 1.0)
WEIGHT_SENT = _env_f("WEIGHT_SENT", _env_f("SENT_WEIGHT", 1.0))

def _score_from_indicators(ind: dict) -> float:
    close   = _as_float(ind.get("close"))
    rsi     = _as_float(ind.get("rsi"))
    macd_h  = _as_float(ind.get("hist"), _as_float(ind.get("macd")))
    ema20   = _as_float(ind.get("ema20"))
    ema50   = _as_float(ind.get("ema50"))
    bb_mid  = _as_float(ind.get("bb_mid"))
    bb_hi   = _as_float(ind.get("bb_hi"))
    stochK  = _as_float(ind.get("stochK"))
    stochD  = _as_float(ind.get("stochD"))
    adx     = _as_float(ind.get("adx"))
    pdi     = _as_float(ind.get("pdi"))
    mdi     = _as_float(ind.get("mdi"))
    atr_rel = _as_float(ind.get("atr_rel"))
    cci     = _as_float(ind.get("cci"))

    # normalizações 0..1
    rsi_n   = _clip01((rsi - 30.0) / 40.0)
    macd_n  = _clip01(0.5 + 0.5 * (math.tanh(macd_h / (abs(macd_h) + 1e-9))))
    ema_sp  = (ema20 - ema50) / (abs(ema50) + 1e-9)
    ema_n   = _clip01(0.5 + 0.5 * math.tanh(ema_sp * 5.0))
    bb_rng  = (bb_hi - bb_mid)
    bb_pos  = (close - bb_mid) / (abs(bb_rng) + 1e-9)
    bb_n    = _clip01(0.5 + 0.5 * math.tanh((bb_pos - 0.2) * 2.0))
    stoch_n = _clip01(stochK)
    dmi_dir = (pdi - mdi) / (abs(pdi) + abs(mdi) + 1e-9)
    adx_s   = _clip01(adx / 50.0)
    adx_n   = _clip01(0.5 + 0.5 * dmi_dir * adx_s)
    atr_n   = _clip01(1.0 - atr_rel)
    cci_n   = _clip01(0.5 + 0.5 * math.tanh(cci / 100.0))

    parts = [W_RSI*rsi_n, W_MACD*macd_n, W_EMA*ema_n, W_BB*bb_n]
    if EN_STOCHRSI: parts.append(W_STOCHRSI*stoch_n)
    if EN_ADX:      parts.append(W_ADX*adx_n)
    if EN_ATR:      parts.append(W_ATR*atr_n)
    if EN_CCI:      parts.append(W_CCI*cci_n)

    w_sum = (
        W_RSI + W_MACD + W_EMA + W_BB +
        (W_STOCHRSI if EN_STOCHRSI else 0.0) +
        (W_ADX if EN_ADX else 0.0) +
        (W_ATR if EN_ATR else 0.0) +
        (W_CCI if EN_CCI else 0.0)
    )
    if w_sum <= 0: return 0.0
    return _clip01(sum(parts) / w_sum)

def score_signal(ohlc_slice):
    try:
        ind = None
        if isinstance(ohlc_slice, list) and ohlc_slice and isinstance(ohlc_slice[-1], dict):
            ind = ohlc_slice[-1].get("ind") or ohlc_slice[-1].get("indicators")
        if ind is None:
            last = ohlc_slice[-1] if ohlc_slice else {}
            ind = {"close": _as_float(last.get("c") or last.get("close"), 0.0)}

        tech = _score_from_indicators(ind)

        sent_news = _as_float(ind.get("sent_news"), 0.5)
        sent_tw   = _as_float(ind.get("sent_twitter"), 0.5)
        sent_list = [v for v in (sent_news, sent_tw) if v >= 0.0]
        sent = sum(sent_list)/len(sent_list) if sent_list else 0.5

        mix = (tech*WEIGHT_TECH + sent*WEIGHT_SENT) / (WEIGHT_TECH + WEIGHT_SENT)
        return {"tech": _clip01(tech), "sent": _clip01(sent), "mix": _clip01(mix)}
    except Exception:
        mix = (0.0*WEIGHT_TECH + 0.5*WEIGHT_SENT) / (WEIGHT_TECH + WEIGHT_SENT)
        return {"tech": 0.0, "sent": 0.5, "mix": _clip01(mix)}
