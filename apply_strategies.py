# -*- coding: utf-8 -*-
"""
apply_strategies.py
- Calcula score técnico (0..1) e gera um plano simples (entry/tp/sl)
- Compatível com main.py: aceita extra_log e extra_weight sem quebrar
"""

from typing import List, Dict, Any, Tuple
import math

# ========= helpers básicos (sem dependências externas) =========

def _sma(vals: List[float], period: int) -> float:
    if len(vals) < period or period <= 0:
        return float("nan")
    return sum(vals[-period:]) / float(period)

def _ema(vals: List[float], period: int) -> float:
    if len(vals) < period or period <= 0:
        return float("nan")
    k = 2.0 / (period + 1.0)
    ema = _sma(vals[:period], period)
    for v in vals[period:]:
        ema = v * k + ema * (1.0 - k)
    return ema

def _std(vals: List[float], period: int) -> float:
    if len(vals) < period:
        return float("nan")
    s = _sma(vals[-period:], period)
    var = sum((x - s) ** 2 for x in vals[-period:]) / float(period)
    return math.sqrt(var)

def _rsi(vals: List[float], period: int = 14) -> float:
    if len(vals) < period + 1:
        return float("nan")
    gains, losses = 0.0, 0.0
    for i in range(-period, 0):
        ch = vals[i] - vals[i - 1]
        if ch > 0:
            gains += ch
        else:
            losses -= ch
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100.0 - (100.0 / (1.0 + rs))

def _atr(ohlc: List[Dict[str, float]], period: int = 14) -> float:
    """ATR clássico (true range médio)."""
    if len(ohlc) < period + 1:
        return float("nan")
    trs: List[float] = []
    for i in range(1, len(ohlc)):
        h = float(ohlc[i]["high"])
        l = float(ohlc[i]["low"])
        pc = float(ohlc[i - 1]["close"])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return float("nan")
    return sum(trs[-period:]) / float(period)

# ========= indicadores “extra” (opcionais) =========
# Se existir o arquivo indicators_extra.py, usamos. Se não, seguimos sem eles.
try:
    from indicators_extra import ichimoku, parabolic_sar, stochastic, vwap, obv  # type: ignore
    _HAS_EXTRA = True
except Exception:
    _HAS_EXTRA = False

# ========= cálculo do score =========

def _base_tech_components(ohlc: List[Dict[str, float]]) -> Dict[str, float]:
    closes = [float(x["close"]) for x in ohlc]
    score_bits = {}

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd = ema12 - ema26 if not math.isnan(ema12) and not math.isnan(ema26) else float("nan")
    sig9 = _ema([macd] * 9 + [macd], 9) if not math.isnan(macd) else float("nan")  # aproxima

    ema50 = _ema(closes, 50)
    ema200 = _ema(closes, 200)

    rsi14 = _rsi(closes, 14)

    bb_mid = _sma(closes, 20)
    bb_std = _std(closes, 20)
    last = closes[-1]

    # regras simples -> pontinhos 0..1
    pts = 0.0
    total = 0.0

    # MACD acima da linha de sinal
    total += 1
    if not math.isnan(macd) and not math.isnan(sig9) and macd >= sig9:
        pts += 1
    score_bits["macd"] = 1.0 if not math.isnan(macd) and not math.isnan(sig9) and macd >= sig9 else 0.0

    # Preço acima da EMA50
    total += 1
    if not math.isnan(ema50) and last >= ema50:
        pts += 1
    score_bits["ema50"] = 1.0 if not math.isnan(ema50) and last >= ema50 else 0.0

    # EMA50 acima da EMA200 (tendência primária)
    total += 1
    if not math.isnan(ema50) and not math.isnan(ema200) and ema50 >= ema200:
        pts += 1
    score_bits["ema_trend"] = 1.0 if not math.isnan(ema50) and not math.isnan(ema200) and ema50 >= ema200 else 0.0

    # RSI saudável (50–70)
    total += 1
    if not math.isnan(rsi14) and 50 <= rsi14 <= 70:
        pts += 1
    score_bits["rsi"] = 1.0 if not math.isnan(rsi14) and 50 <= rsi14 <= 70 else 0.0

    # Preço acima da BB média
    total += 1
    if not math.isnan(bb_mid) and last >= bb_mid:
        pts += 1
    score_bits["bb"] = 1.0 if not math.isnan(bb_mid) and last >= bb_mid else 0.0

    base = pts / total if total > 0 else 0.0
    score_bits["base"] = base
    return score_bits

def _extra_components(ohlc: List[Dict[str, float]]) -> Dict[str, float]:
    """Componentes extra (Ichimoku, SAR, Stoch, VWAP, OBV). 0..1 cada."""
    res = {"ichimoku": 0.0, "sar": 0.0, "stoch": 0.0, "vwap": 0.0, "obv": 0.0}
    if not _HAS_EXTRA:
        return res
    closes = [float(x["close"]) for x in ohlc]
    highs = [float(x["high"]) for x in ohlc]
    lows  = [float(x["low"]) for x in ohlc]

    try:
        ich = ichimoku(highs, lows, closes)  # retorno boolean/score
        res["ichimoku"] = 1.0 if ich else 0.0
    except Exception:
        pass
    try:
        psar_up = parabolic_sar(highs, lows, closes)  # True=compra
        res["sar"] = 1.0 if psar_up else 0.0
    except Exception:
        pass
    try:
        st = stochastic(highs, lows, closes)  # retorna (k, d)
        k, d = st if isinstance(st, (list, tuple)) and len(st) >= 2 else (0.0, 0.0)
        res["stoch"] = 1.0 if k > d and k < 80 else 0.0
    except Exception:
        pass
    try:
        above = vwap(ohlc)  # True se preço acima do VWAP
        res["vwap"] = 1.0 if above else 0.0
    except Exception:
        pass
    try:
        obv_dir = obv(ohlc)  # True se OBV ascendendo
        res["obv"] = 1.0 if obv_dir else 0.0
    except Exception:
        pass
    return res

def score_signal(
    prices: List[Dict[str, float]],
    use_ai: bool = False,
    ai_model: Any = None,
    extra_weight: float = 0.0,
    extra_log: bool = False,
    **kwargs
) -> float:
    """
    Retorna score 0..1. Combina:
      - base técnico (MACD/EMA/RSI/BB)
      - extras (Ichimoku, SAR, Stoch, VWAP, OBV) com peso 'extra_weight'
      - IA (se houver), suavemente (média ponderada)
    Aceita extra_log para debug sem quebrar o main.
    """
    if not prices or len(prices) < 30:
        return 0.0

    bits = _base_tech_components(prices)
    base = bits["base"]

    extras = _extra_components(prices)
    if extra_weight > 0.0:
        ex_vals = list(extras.values())
        ex_avg = sum(ex_vals) / len(ex_vals) if ex_vals else 0.0
        tech = max(0.0, min(1.0, (1 - extra_weight) * base + extra_weight * ex_avg))
    else:
        tech = base

    # componente IA (opcional)
    if use_ai and ai_model is not None:
        try:
            closes = [float(x["close"]) for x in prices]
            feat = [
                closes[-1],
                _ema(closes, 12) or 0.0,
                _ema(closes, 26) or 0.0,
                _rsi(closes, 14) or 0.0,
            ]
            ai_raw = float(ai_model.predict_proba([feat])[0][1])  # 0..1
        except Exception:
            ai_raw = 0.5
        score = 0.5 * tech + 0.5 * ai_raw
    else:
        score = tech

    score = max(0.0, min(1.0, score))

    if extra_log:
        print(f"[EXTRA] base={base:.2f} extras={extras} final={score:.2f}")

    return score

# ========= geração de sinal simples =========

def _plan_long(ohlc: List[Dict[str, float]], rr: float = 2.0) -> Tuple[float, float, float]:
    """Entry = close, SL = close - 1*ATR, TP ajustado para R:R desejado."""
    closes = [float(x["close"]) for x in ohlc]
    last = closes[-1]
    atr = _atr(ohlc, 14)
    if math.isnan(atr) or atr <= 0:
        atr = last * 0.005  # fallback 0,5%
    sl = last - atr
    tp = last + rr * (last - sl)
    return last, tp, sl

def generate_signal(
    prices: List[Dict[str, float]],
    rr: float = 2.0,
    extra_log: bool = False,
    **kwargs
) -> Dict[str, Any] | None:
    """
    Gera um sinal LONG básico quando o conjunto técnico está positivo:
      - MACD>signal, preço>EMA50, EMA50>EMA200, RSI entre 50-70, preço>BB mid.
    Retorna dict com: entry, tp, sl, rr, strategy.
    """
    if not prices or len(prices) < 30:
        return None

    bits = _base_tech_components(prices)
    ok = (
        bits.get("macd", 0.0) > 0.5 and
        bits.get("ema50", 0.0) > 0.5 and
        bits.get("ema_trend", 0.0) > 0.5 and
        bits.get("rsi", 0.0) > 0.5 and
        bits.get("bb", 0.0) > 0.5
    )

    # Se tiver extras, pedimos pelo menos 2/5 positivos — deixa mais criterioso.
    if _HAS_EXTRA:
        ex = _extra_components(prices)
        if sum(1 for v in ex.values() if v > 0.5) < 2:
            ok = False
        if extra_log:
            print(f"[EXTRA] gate extras={ex} ok={ok}")

    if not ok:
        return None

    entry, tp, sl = _plan_long(prices, rr=rr)
    return {
        "entry": float(entry),
        "tp": float(tp),
        "sl": float(sl),
        "rr": float(rr),
        "strategy": "RSI+MACD+EMA+BB+EXTRA" if _HAS_EXTRA else "RSI+MACD+EMA+BB",
    }
