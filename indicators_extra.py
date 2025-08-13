# -*- coding: utf-8 -*-

# --- Ichimoku ---
def ichimoku(highs, lows, conv=9, base=26, span_b=52):
    n = len(highs)
    conv_line = [None]*n
    base_line = [None]*n
    span_a    = [None]*n
    span_b_a  = [None]*n
    for i in range(n):
        if i+1 >= conv:
            h = max(highs[i-conv+1:i+1]); l = min(lows[i-conv+1:i+1])
            conv_line[i] = (h+l)/2.0
        if i+1 >= base:
            h = max(highs[i-base+1:i+1]); l = min(lows[i-base+1:i+1])
            base_line[i] = (h+l)/2.0
        if conv_line[i] is not None and base_line[i] is not None:
            span_a[i] = (conv_line[i] + base_line[i]) / 2.0
        if i+1 >= span_b:
            h = max(highs[i-span_b+1:i+1]); l = min(lows[i-span_b+1:i+1])
            span_b_a[i] = (h+l)/2.0
    return conv_line, base_line, span_a, span_b_a

# --- Parabolic SAR (simplificado e robusto) ---
def parabolic_sar(highs, lows, step=0.02, max_step=0.2):
    n = len(highs)
    if n < 3: return [None]*n
    psar = [None]*n
    uptrend = True
    af = step
    ep = highs[0]
    psar[1] = lows[0]
    for i in range(2, n):
        prev = psar[i-1]
        if uptrend:
            psar[i] = prev + af*(ep - prev)
            psar[i] = min(psar[i], lows[i-1], lows[i-2])
            if highs[i] > ep:
                ep = highs[i]; af = min(af + step, max_step)
            if lows[i] < psar[i]:
                uptrend = False
                psar[i] = ep
                ep = lows[i]
                af = step
        else:
            psar[i] = prev + af*(ep - prev)
            psar[i] = max(psar[i], highs[i-1], highs[i-2])
            if lows[i] < ep:
                ep = lows[i]; af = min(af + step, max_step)
            if highs[i] > psar[i]:
                uptrend = True
                psar[i] = ep
                ep = highs[i]
                af = step
    return psar

# --- Stochastic Oscillator %K/%D ---
def stochastic(highs, lows, closes, k_period=14, d_period=3):
    n = len(closes)
    K = [None]*n; D=[None]*n
    for i in range(n):
        if i+1 >= k_period:
            hi = max(highs[i-k_period+1:i+1])
            lo = min(lows[i-k_period+1:i+1])
            rng = (hi - lo) if (hi - lo) != 0 else 1e-9
            K[i] = (closes[i] - lo)/rng * 100.0
    for i in range(n):
        if i+1 >= d_period and K[i] is not None:
            vals = [v for v in K[i-d_period+1:i+1] if v is not None]
            if len(vals)==d_period:
                D[i] = sum(vals)/d_period
    return K, D

# --- VWAP (requer volume) ---
def vwap(highs, lows, closes, volumes):
    n = len(closes)
    if not volumes or len(volumes)!=n: return [None]*n
    vwap_arr = [None]*n
    pv = 0.0; vv=0.0
    for i in range(n):
        tp = (highs[i] + lows[i] + closes[i]) / 3.0
        v  = float(volumes[i] or 0.0)
        pv += tp * v; vv += v
        vwap_arr[i] = (pv / vv) if vv>0 else None
    return vwap_arr

# --- OBV (requer volume) ---
def obv(closes, volumes):
    n = len(closes)
    if not volumes or len(volumes)!=n: return [None]*n
    obv_arr = [0.0]*n
    for i in range(1,n):
        if closes[i] > closes[i-1]:
            obv_arr[i] = obv_arr[i-1] + volumes[i]
        elif closes[i] < closes[i-1]:
            obv_arr[i] = obv_arr[i-1] - volumes[i]
        else:
            obv_arr[i] = obv_arr[i-1]
    return obv_arr
