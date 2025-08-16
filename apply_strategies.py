# -*- coding: utf-8 -*-
"""
apply_strategies.py
- Calcula indicadores técnicos e retorna um score 0..1
- Gera um plano simples de trade (entry/tp/sl) quando o score for bom
Obs.: não usa libs externas (só python puro), para rodar no Railway.
"""

import os
import math
from typing import List, Dict, Any

# =========================
# Config por ENV (pesos)
# =========================
def _b(v: str, default: bool = True) -> bool:
    return os.getenv(v, "true" if default else "false").strip().lower() in ("1","true","yes","y","on")

W_RSI       = float(os.getenv("W_RSI",       "1.0"))
W_MACD      = float(os.getenv("W_MACD",      "1.0"))
W_EMA       = float(os.getenv("W_EMA",       "1.0"))
W_BB        = float(os.getenv("W_BB",        "0.7"))
W_STOCHRSI  = float(os.getenv("W_STOCHRSI",  "0.8"))
W_ADX       = float(os.getenv("W_ADX",       "0.8"))
W_ATR       = float(os.getenv("W_ATR",       "0.0"))  # por padrão não influencia
W_CCI       = float(os.getenv("W_CCI",       "0.5"))

EN_STOCHRSI = _b("EN_STOCHRSI", True)
EN_ADX      = _b("EN_ADX",      True)
EN_ATR      = _b("EN_ATR",      False)
EN_CCI      = _b("EN_CCI",      True)

DEBUG_IND   = _b("DEBUG_INDICATORS", False)

# =========================
# Helpers de séries
# =========================
def _clamp(x: float, a: float, b: float) -> float:
    return max(a, min(b, x))

def _ema(values: List[float], period: int) -> List[float]:
    if period <= 1 or len(values) == 0:
        return values[:]
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(out[-1] + k * (values[i] - out[-1]))
    return out

def _sma(values: List[float], period: int) -> List[float]:
    out = []
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i-period]
        if i >= period-1:
            out.append(s/period)
        else:
            out.append(float('nan'))
    return out

def _diff(a: List[float], b: List[float]) -> List[float]:
    return [ (a[i]-b[i]) if (i < len(a) and i < len(b)) else float('nan') for i in range(max(len(a), len(b))) ]

def _rsi(closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < period + 1:
        return [float('nan')] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i-1]
        gains.append(max(0.0, ch))
        losses.append(max(0.0, -ch))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsis = [float('nan')] * len(closes)
    # primeira posição válida é no índice period
    rsis[period] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain/avg_loss)))
    for i in range(period+1, len(closes)):
        gain = gains[i-1]
        loss = losses[i-1]
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
        rsis[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + (avg_gain/avg_loss)))
    return rsis

def _macd(closes: List[float], fast: int=12, slow: int=26, sig: int=9):
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    signal = _ema(macd_line, sig)
    hist = [macd_line[i] - signal[i] for i in range(len(closes))]
    return macd_line, signal, hist

def _bollinger(closes: List[float], period: int=20, ndev: float=2.0):
    sma = _sma(closes, period)
    stds = []
    from collections import deque
    window = deque()
    s = 0.0
    s2 = 0.0
    for i, v in enumerate(closes):
        window.append(v)
        s += v
        s2 += v*v
        if len(window) > period:
            old = window.popleft()
            s -= old
            s2 -= old*old
        if len(window) == period:
            mean = s / period
            var = max(0.0, (s2/period) - (mean*mean))
            stds.append(math.sqrt(var))
        else:
            stds.append(float('nan'))
    upper = [ (sma[i] + ndev*stds[i]) if not math.isnan(sma[i]) and not math.isnan(stds[i]) else float('nan') for i in range(len(closes)) ]
    lower = [ (sma[i] - ndev*stds[i]) if not math.isnan(sma[i]) and not math.isnan(stds[i]) else float('nan') for i in range(len(closes)) ]
    return lower, sma, upper

def _true_range(h: List[float], l: List[float], c: List[float]) -> List[float]:
    tr = [float('nan')]
    for i in range(1, len(c)):
        tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    return tr

def _atr(h: List[float], l: List[float], c: List[float], period: int=14) -> List[float]:
    tr = _true_range(h,l,c)
    # usa EMA do TR
    atr = []
    alpha = 1.0/period
    prev = None
    for v in tr:
        if math.isnan(v):
            atr.append(float('nan'))
        else:
            if prev is None:
                prev = v
            else:
                prev = prev + alpha*(v - prev)
            atr.append(prev)
    return atr

def _adx(h: List[float], l: List[float], c: List[float], period: int=14):
    # +DM / -DM
    plus_dm, minus_dm, tr = [], [], []
    for i in range(len(c)):
        if i == 0:
            plus_dm.append(0.0); minus_dm.append(0.0)
            tr.append(0.0)
        else:
            up = h[i] - h[i-1]
            dw = l[i-1] - l[i]
            plus_dm.append(up if (up > dw and up > 0) else 0.0)
            minus_dm.append(dw if (dw > up and dw > 0) else 0.0)
            tr.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))

    def _ema_arr(a: List[float], p: int) -> List[float]:
        k = 2.0/(p+1.0)
        out = []
        prev = None
        for v in a:
            if prev is None:
                prev = v
            else:
                prev = prev + k*(v-prev)
            out.append(prev)
        return out

    atr = _ema_arr(tr, period)
    pdi = [ (plus_dm[i]/atr[i]*100.0) if atr[i] > 0 else 0.0 for i in range(len(c)) ]
    mdi = [ (minus_dm[i]/atr[i]*100.0) if atr[i] > 0 else 0.0 for i in range(len(c)) ]
    dx  = [ (abs(pdi[i]-mdi[i])/(pdi[i]+mdi[i])*100.0) if (pdi[i]+mdi[i])>0 else 0.0 for i in range(len(c)) ]
    # suaviza DX para virar ADX
    adx = _ema_arr(dx, period)
    return pdi, mdi, adx

def _cci(h: List[float], l: List[float], c: List[float], period: int=20) -> List[float]:
    tp = [ (h[i]+l[i]+c[i])/3.0 for i in range(len(c)) ]
    sma_tp = _sma(tp, period)
    dev = []
    from collections import deque
    window = deque()
    for i, v in enumerate(tp):
        window.append(v)
        if len(window) > period:
            window.popleft()
        if len(window) == period and not math.isnan(sma_tp[i]):
            mean = sma_tp[i]
            mean_dev = sum(abs(x-mean) for x in list(window)) / period
            dev.append(mean_dev)
        else:
            dev.append(float('nan'))
    cci = []
    for i in range(len(c)):
        if math.isnan(sma_tp[i]) or math.isnan(dev[i]) or dev[i] == 0:
            cci.append(float('nan'))
        else:
            cci.append( (tp[i]-sma_tp[i]) / (0.015*dev[i]) )
    return cci

# =========================
# Normalização de OHLC do seu fetcher
# Espera lista de dicts: {"time","open","high","low","close"}
# =========================
def _to_columns(ohlc: List[Dict[str, Any]]):
    o = [float(x["open"])  for x in ohlc]
    h = [float(x["high"])  for x in ohlc]
    l = [float(x["low"])   for x in ohlc]
    c = [float(x["close"]) for x in ohlc]
    return o,h,l,c

# =========================
# Score principal
# =========================
def score_signal(ohlc: List[Dict[str, Any]]) -> float:
    """Retorna um score 0..1 combinando vários indicadores."""
    try:
        o,h,l,c = _to_columns(ohlc)
        n = len(c)
        if n < 40:
            return 0.0

        # Indicadores
        rsi = _rsi(c, 14)
        macd, macd_sig, macd_hist = _macd(c)
        ema20 = _ema(c, 20)
        ema50 = _ema(c, 50)
        bb_lo, bb_mid, bb_hi = _bollinger(c, 20, 2.0)
        if EN_STOCHRSI:
            # StochRSI (K e D)
            rsi14 = _rsi(c, 14)
            # normaliza rsi 0..1 com janela 14
            k_vals = []
            for i in range(n):
                j0 = max(0, i-13)
                win = [x for x in rsi14[j0:i+1] if not math.isnan(x)]
                if len(win) < 1:
                    k_vals.append(float('nan'))
                else:
                    mn = min(win); mx = max(win)
                    k_vals.append( ( (rsi14[i]-mn)/(mx-mn) ) if (mx>mn) else 0.0 )
            d_vals = _sma([x if not math.isnan(x) else 0.0 for x in k_vals], 3)
        else:
            k_vals = [float('nan')]*n
            d_vals = [float('nan')]*n

        if EN_ADX:
            pdi, mdi, adx = _adx(h,l,c,14)
        else:
            pdi, mdi, adx = ([float('nan')]*n,)*3

        if EN_ATR:
            atr = _atr(h,l,c,14)
        else:
            atr = [float('nan')]*n

        if EN_CCI:
            cci = _cci(h,l,c,20)
        else:
            cci = [float('nan')]*n

        i = n-1  # vela atual
        close = c[i]

        # Sub-scores 0..1
        sub_scores = []
        weights    = []

        # RSI: quanto acima de 50 (até 90)
        if not math.isnan(rsi[i]):
            rsi_comp = _clamp((rsi[i]-50.0)/40.0, 0.0, 1.0)
            sub_scores.append(rsi_comp); weights.append(W_RSI)

        # MACD: linha acima do sinal e hist positivo
        macd_comp = 1.0 if (macd[i] > macd_sig[i] and macd_hist[i] > 0) else (0.5 if macd[i] > macd_sig[i] else 0.0)
        sub_scores.append(macd_comp); weights.append(W_MACD)

        # EMAs: tendência curta > longa e preço acima da ema20
        if not math.isnan(ema20[i]) and not math.isnan(ema50[i]):
            ema_comp = 1.0 if (ema20[i] > ema50[i] and close > ema20[i]) else 0.0
            sub_scores.append(ema_comp); weights.append(W_EMA)

        # BB: acima da banda média (tendência), mas não muito esticado (abaixo da upper)
        if not math.isnan(bb_mid[i]) and not math.isnan(bb_hi[i]):
            if close >= bb_mid[i] and close <= bb_hi[i]:
                bb_comp = 1.0
            elif close > bb_hi[i]:
                bb_comp = 0.4  # overbought/esticado
            else:
                bb_comp = 0.0
            sub_scores.append(bb_comp); weights.append(W_BB)

        # StochRSI: cruzamento K>D e K < 0.8
        if EN_STOCHRSI and (not math.isnan(k_vals[i]) and not math.isnan(d_vals[i])):
            stoch_comp = 1.0 if (k_vals[i] > d_vals[i] and k_vals[i] < 0.8) else 0.0
            sub_scores.append(stoch_comp); weights.append(W_STOCHRSI)

        # ADX: força de tendência +DI > -DI e ADX > 20
        if EN_ADX and (not math.isnan(adx[i])):
            adx_comp = 1.0 if (adx[i] > 20.0 and pdi[i] > mdi[i]) else 0.0
            sub_scores.append(adx_comp); weights.append(W_ADX)

        # ATR (opcional): menor volatilidade relativa favorece score (mais “limpo”)
        if EN_ATR and (not math.isnan(atr[i]) and close > 0):
            atr_rel = atr[i] / close  # ~0.005 é ok; acima de 0.02 muito volátil
            atr_comp = _clamp( (0.02 - atr_rel) / 0.02, 0.0, 1.0 )
            sub_scores.append(atr_comp); weights.append(W_ATR)

        # CCI: acima de 0 tende positivo
        if EN_CCI and (not math.isnan(cci[i])):
            cci_comp = 1.0 if cci[i] > 0 else 0.0
            sub_scores.append(cci_comp); weights.append(W_CCI)

        total_w = sum(w for w in weights if w > 0)
        if total_w <= 0:
            return 0.0
        score = sum(sub_scores[j]*weights[j] for j in range(len(sub_scores))) / total_w
        score = _clamp(score, 0.0, 1.0)

        if DEBUG_IND:
            print(f"[IND] close={round(close,6)} | rsi={round(rsi[i],2) if not math.isnan(rsi[i]) else None} | "
                  f"macd={round(macd[i],6)}>{round(macd_sig[i],6)} hist={round(macd_hist[i],6)} | "
                  f"ema20={round(ema20[i],6)} ema50={round(ema50[i],6)} | "
                  f"bb_mid={round(bb_mid[i],6) if not math.isnan(bb_mid[i]) else None} "
                  f"bb_hi={round(bb_hi[i],6) if not math.isnan(bb_hi[i]) else None} | "
                  f"stochK={round(k_vals[i],3) if not math.isnan(k_vals[i]) else None} "
                  f"stochD={round(d_vals[i],3) if not math.isnan(d_vals[i]) else None} | "
                  f"adx={round(adx[i],2) if EN_ADX else None} pdi={round(pdi[i],2) if EN_ADX else None} mdi={round(mdi[i],2) if EN_ADX else None} | "
                  f"atr_rel={round((atr[i]/close),4) if EN_ATR and close>0 and not math.isnan(atr[i]) else None} | "
                  f"cci={round(cci[i],2) if EN_CCI else None} | "
                  f"score={round(score*100,1)}%")

        return float(score)

    except Exception as e:
        if DEBUG_IND:
            print(f"[IND] erro em score_signal: {e}")
        return 0.0

# =========================
# Geração de sinal simples
# =========================
def generate_signal(ohlc: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Estratégia básica: entrada no close atual; TP/SL por ATR (se disponível) ou 1.5%/0.75%.
    """
    o,h,l,c = _to_columns(ohlc)
    i = len(c) - 1
    price = float(c[i])

    # ATR para dimensionar SL/TP, se disponível
    atr = _atr(h,l,c,14)
    if not math.isnan(atr[i]) and atr[i] > 0:
        sl = price - 1.0 * atr[i]
        tp = price + 2.0 * atr[i]
        rr = (tp - price) / (price - sl) if (price - sl) > 0 else 2.0
    else:
        # fallback fixo
        tp = price * 1.015
        sl = price * 0.9925
        rr = 2.0

    return {
        "entry": price,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "strategy": "RSI+MACD+EMA+BB+STOCH+ADX+CCI"
    }
