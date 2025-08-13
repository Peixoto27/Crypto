# -*- coding: utf-8 -*-
# indicators_extra.py — compatível com apply_strategies
# Fornece: stochastic, ichimoku, parabolic_sar, vwap, obv

from typing import List, Tuple, Optional

# ---------------------------
# Stochastic Oscillator (%K/%D)
# ---------------------------
def stochastic(highs: List[float], lows: List[float], closes: List[float],
               k_period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    """Retorna listas %K e %D (médias simples)."""
    n = len(closes)
    k_raw: List[Optional[float]] = [None] * n

    for i in range(n):
        if i + 1 < k_period:
            continue
        wnd_h = max(highs[i - k_period + 1:i + 1])
        wnd_l = min(lows[i - k_period + 1:i + 1])
        den = (wnd_h - wnd_l) or 1e-12
        k_raw[i] = (closes[i] - wnd_l) / den * 100.0

    # suaviza %K
    k_s: List[Optional[float]] = [None] * n
    for i in range(n):
        if i + 1 < smooth_k:
            continue
        vals = [v for v in k_raw[i - smooth_k + 1:i + 1] if v is not None]
        k_s[i] = (sum(vals) / len(vals)) if vals else None

    # %D é média de %K
    d_s: List[Optional[float]] = [None] * n
    for i in range(n):
        if i + 1 < smooth_d:
            continue
        vals = [v for v in k_s[i - smooth_d + 1:i + 1] if v is not None]
        d_s[i] = (sum(vals) / len(vals)) if vals else None

    return k_s, d_s

# ---------------------------
# Ichimoku (sem shift futuro)
# ---------------------------
def ichimoku(highs: List[float], lows: List[float],
             conv_len: int = 9, base_len: int = 26, spanb_len: int = 52):
    """
    Retorna (tenkan, kijun, senkou_a, senkou_b), alinhados no fim (sem projeção +26).
    """
    def mid(h, l): return (h + l) / 2.0
    n = len(highs)
    tenkan = [None] * n
    kijun  = [None] * n
    span_a = [None] * n
    span_b = [None] * n

    for i in range(n):
        if i + 1 >= conv_len:
            hh = max(highs[i - conv_len + 1:i + 1])
            ll = min(lows [i - conv_len + 1:i + 1])
            tenkan[i] = mid(hh, ll)
        if i + 1 >= base_len:
            hh = max(highs[i - base_len + 1:i + 1])
            ll = min(lows [i - base_len + 1:i + 1])
            kijun[i] = mid(hh, ll)
        if tenkan[i] is not None and kijun[i] is not None:
            span_a[i] = (tenkan[i] + kijun[i]) / 2.0
        if i + 1 >= spanb_len:
            hh = max(highs[i - spanb_len + 1:i + 1])
            ll = min(lows [i - spanb_len + 1:i + 1])
            span_b[i] = mid(hh, ll)

    return tenkan, kijun, span_a, span_b

# ---------------------------
# Parabolic SAR (simplificado)
# ---------------------------
def parabolic_sar(highs: List[float], lows: List[float],
                  step: float = 0.02, max_step: float = 0.2) -> List[Optional[float]]:
    n = len(highs)
    if n == 0:
        return []
    psar: List[Optional[float]] = [None] * n
    bull = True
    af = step
    ep = highs[0]
    ps = lows[0]
    psar[0] = ps

    for i in range(1, n):
        prev = psar[i - 1] if psar[i - 1] is not None else ps
        psar[i] = prev + af * (ep - prev)

        if bull:
            if lows[i] < psar[i]:
                bull = False
                psar[i] = ep
                ep = lows[i]
                af = step
            else:
                if highs[i] > ep:
                    ep = highs[i]
                    af = min(max_step, af + step)
        else:
            if highs[i] > psar[i]:
                bull = True
                psar[i] = ep
                ep = highs[i]
                af = step
            else:
                if lows[i] < ep:
                    ep = lows[i]
                    af = min(max_step, af + step)

    return psar

# ---------------------------
# VWAP (precisa de volumes)
# ---------------------------
def vwap(highs: List[float], lows: List[float], closes: List[float], volumes: Optional[List[float]] = None) -> List[Optional[float]]:
    """
    VWAP = soma(Típico*Vol) / soma(Vol)
    Se volumes for None ou vazio, retorna lista de None (evita crash).
    """
    n = len(closes)
    if not volumes or len(volumes) != n:
        return [None] * n

    vwap_series: List[Optional[float]] = [None] * n
    pv_cum = 0.0
    v_cum  = 0.0
    for i in range(n):
        tp = (highs[i] + lows[i] + closes[i]) / 3.0
        vol = max(float(volumes[i]), 0.0)
        pv_cum += tp * vol
        v_cum  += vol
        vwap_series[i] = (pv_cum / v_cum) if v_cum > 0 else None
    return vwap_series

# ---------------------------
# OBV (precisa de volumes)
# ---------------------------
def obv(closes: List[float], volumes: Optional[List[float]] = None) -> List[Optional[float]]:
    """
    On-Balance Volume. Se volumes ausentes, devolve None para não quebrar.
    """
    n = len(closes)
    if not volumes or len(volumes) != n:
        return [None] * n

    out: List[Optional[float]] = [0.0] + [None] * (n - 1)
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out
