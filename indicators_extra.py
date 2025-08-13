# -*- coding: utf-8 -*-
# indicators_extra.py — indicadores “passivos” (Stochastic, Ichimoku, Parabolic SAR)
# Não alteram o score, a menos que você queira (via EXTRA_SCORE_WEIGHT > 0).
from typing import List, Tuple

def stochastic_kd(highs: List[float], lows: List[float], closes: List[float],
                  k_period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> Tuple[List[float], List[float]]:
    """Stochastic %K e %D (simples)"""
    k_raw: List[float] = []
    for i in range(len(closes)):
        if i + 1 < k_period:
            k_raw.append(None); continue
        window_h = max(highs[i - k_period + 1:i + 1])
        window_l = min(lows[i - k_period + 1:i + 1])
        den = (window_h - window_l) or 1e-12
        k_raw.append((closes[i] - window_l) / den * 100.0)

    # suaviza %K
    k_s: List[float] = []
    for i in range(len(k_raw)):
        if k_raw[i] is None or i + 1 < smooth_k:
            k_s.append(None); continue
        vals = [v for v in k_raw[i - smooth_k + 1:i + 1] if v is not None]
        k_s.append(sum(vals) / len(vals) if vals else None)

    # %D = média de %K
    d_s: List[float] = []
    for i in range(len(k_s)):
        if k_s[i] is None or i + 1 < smooth_d:
            d_s.append(None); continue
        vals = [v for v in k_s[i - smooth_d + 1:i + 1] if v is not None]
        d_s.append(sum(vals) / len(vals) if vals else None)

    return k_s, d_s

def ichimoku(highs: List[float], lows: List[float],
             conv_len: int = 9, base_len: int = 26, spanb_len: int = 52):
    """Retorna (tenkan, kijun, senkou_a, senkou_b) alinhados no fim (sem shift futuro)"""
    def mid(hh, ll): return (hh + ll) / 2.0
    n = len(highs)
    tenkan, kijun, span_a, span_b = [None]*n, [None]*n, [None]*n, [None]*n

    for i in range(n):
        if i + 1 >= conv_len:
            hh = max(highs[i - conv_len + 1:i + 1])
            ll = min(lows[i - conv_len + 1:i + 1])
            tenkan[i] = mid(hh, ll)

        if i + 1 >= base_len:
            hh = max(highs[i - base_len + 1:i + 1])
            ll = min(lows[i - base_len + 1:i + 1])
            kijun[i] = mid(hh, ll)

        if tenkan[i] is not None and kijun[i] is not None:
            span_a[i] = (tenkan[i] + kijun[i]) / 2.0

        if i + 1 >= spanb_len:
            hh = max(highs[i - spanb_len + 1:i + 1])
            ll = min(lows[i - spanb_len + 1:i + 1])
            span_b[i] = mid(hh, ll)

    return tenkan, kijun, span_a, span_b

def parabolic_sar(highs: List[float], lows: List[float],
                  step: float = 0.02, max_step: float = 0.2) -> List[float]:
    """PSAR simplificado"""
    n = len(highs)
    if n == 0:
        return []
    psar = [None] * n
    bull = True  # começa supõe alta
    af = step
    ep = highs[0]  # extreme point
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
