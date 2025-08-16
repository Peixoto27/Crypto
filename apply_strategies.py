# -*- coding: utf-8 -*-
"""
apply_strategies.py
- Calcula indicadores técnicos a partir de OHLC
- Consolida um score técnico (0..1) com pesos configuráveis via ENV
- Gera plano básico de trade (entry/tp/sl) a partir de volatilidade (ATR/BB)

Compatível com OHLC em:
  - list[dict]: {"time","open","high","low","close"}
  - list[list|tuple]: [ts, open, high, low, close]

Variáveis de ambiente (exemplos):
  W_RSI=1.0          EN_STOCHRSI=true
  W_MACD=1.0
  W_EMA=1.0
  W_BB=0.7
  W_STOCHRSI=0.8
  W_ADX=0.8          EN_ADX=true
  W_ATR=0.0          EN_ATR=false
  W_CCI=0.5          EN_CCI=true
  W_ICHI=0.8         EN_ICHI=true
  W_OBV=0.6          EN_OBV=true
  W_MFI=0.6          EN_MFI=true
  W_WILLR=0.5        EN_WILLR=true

  DEBUG_INDICATORS=true

Retornos:
  - score_signal(ohlc) -> (score_float_0_1, details_dict)
  - generate_signal(ohlc) -> dict {entry,tp,sl,rr,strategy,created_at}
"""

import os
import math
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple

import numpy as np


# ==========================
# Leitura de ENV (pesos/flags)
# ==========================
def _fenv(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _benv(name: str, default: bool) -> bool:
    s = os.getenv(name, None)
    if s is None:
        return default
    return str(s).strip().lower() in ("1", "true", "yes", "y", "on")


W_RSI       = _fenv("W_RSI", 1.0)
W_MACD      = _fenv("W_MACD", 1.0)
W_EMA       = _fenv("W_EMA", 1.0)
W_BB        = _fenv("W_BB", 0.7)
W_STOCHRSI  = _fenv("W_STOCHRSI", 0.8)
W_ADX       = _fenv("W_ADX", 0.8)
W_ATR       = _fenv("W_ATR", 0.0)
W_CCI       = _fenv("W_CCI", 0.5)

# Novos
W_ICHI      = _fenv("W_ICHI", 0.8)
W_OBV       = _fenv("W_OBV", 0.6)
W_MFI       = _fenv("W_MFI", 0.6)
W_WILLR     = _fenv("W_WILLR", 0.5)

EN_STOCHRSI = _benv("EN_STOCHRSI", True)
EN_ADX      = _benv("EN_ADX", True)
EN_ATR      = _benv("EN_ATR", False)
EN_CCI      = _benv("EN_CCI", True)

# Novos
EN_ICHI     = _benv("EN_ICHI", True)
EN_OBV      = _benv("EN_OBV", True)
EN_MFI      = _benv("EN_MFI", True)
EN_WILLR    = _benv("EN_WILLR", True)

DEBUG_INDICATORS = _benv("DEBUG_INDICATORS", True)


def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


# ==========================
# Utilidades de OHLC
# ==========================
def _to_arrays(ohlc: List[Any]) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Converte OHLC em arrays numpy (ts, o, h, l, c).
    Aceita lista de dicts ou lista de listas/tuplas.
    """
    if not ohlc:
        raise ValueError("OHLC vazio")

    if isinstance(ohlc[0], dict):
        t = np.array([row.get("time") or row.get("t") or 0 for row in ohlc], dtype=float)
        o = np.array([float(row.get("open")) for row in ohlc], dtype=float)
        h = np.array([float(row.get("high")) for row in ohlc], dtype=float)
        l = np.array([float(row.get("low")) for row in ohlc], dtype=float)
        c = np.array([float(row.get("close")) for row in ohlc], dtype=float)
    else:
        # [ts, open, high, low, close]
        t = np.array([float(r[0]) for r in ohlc], dtype=float)
        o = np.array([float(r[1]) for r in ohlc], dtype=float)
        h = np.array([float(r[2]) for r in ohlc], dtype=float)
        l = np.array([float(r[3]) for r in ohlc], dtype=float)
        c = np.array([float(r[4]) for r in ohlc], dtype=float)

    return t, o, h, l, c


def _ema(x: np.ndarray, period: int) -> np.ndarray:
    if period <= 1:
        return x.copy()
    k = 2.0 / (period + 1.0)
    ema = np.empty_like(x)
    ema[:] = np.nan
    ema[0] = x[0]
    for i in range(1, len(x)):
        ema[i] = x[i] * k + ema[i - 1] * (1 - k)
    return ema


# ==========================
# Indicadores
# ==========================
def ta_rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=close[0])
    up = np.where(delta > 0, delta, 0.0)
    down = np.where(delta < 0, -delta, 0.0)
    ema_up = _ema(up, period)
    ema_down = _ema(down, period)
    rs = ema_up / np.maximum(1e-12, ema_down)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def ta_macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd = ema_fast - ema_slow
    sig = _ema(macd, signal)
    hist = macd - sig
    return macd, sig, hist


def ta_bb(close: np.ndarray, period: int = 20, devs: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(close) < period:
        m = np.full_like(close, np.nan)
        s = np.full_like(close, np.nan)
    else:
        m = np.convolve(close, np.ones(period), 'same') / period
        # std simples (janela centrada aproximada)
        # para bordas, calcula std de janela válida
        s = np.array([np.std(close[max(0, i - period // 2):min(len(close), i + period // 2 + 1)]) for i in range(len(close))])
    upper = m + devs * s
    lower = m - devs * s
    return m, upper, lower


def ta_stochrsi(close: np.ndarray, rsi_period: int = 14, stoch_period: int = 14, smoothK: int = 3, smoothD: int = 3) -> Tuple[np.ndarray, np.ndarray]:
    rsi = ta_rsi(close, rsi_period)
    min_r = np.array([np.min(rsi[max(0, i - stoch_period + 1):i + 1]) for i in range(len(rsi))])
    max_r = np.array([np.max(rsi[max(0, i - stoch_period + 1):i + 1]) for i in range(len(rsi))])
    denom = np.maximum(1e-12, (max_r - min_r))
    stoch = (rsi - min_r) / denom
    k = _ema(stoch, smoothK)
    d = _ema(k, smoothD)
    return k, d


def ta_true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close)
    ])
    return tr


def ta_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = ta_true_range(high, low, close)
    return _ema(tr, period)


def ta_dmi_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    up_move = high - np.roll(high, 1)
    down_move = np.roll(low, 1) - low
    up_move[0] = 0.0
    down_move[0] = 0.0

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    atr = ta_atr(high, low, close, period)
    plus_di = 100.0 * _ema(plus_dm, period) / np.maximum(1e-12, atr)
    minus_di = 100.0 * _ema(minus_dm, period) / np.maximum(1e-12, atr)

    dx = 100.0 * np.abs(plus_di - minus_di) / np.maximum(1e-12, (plus_di + minus_di))
    adx = _ema(dx, period)
    return plus_di, minus_di, adx


def ta_cci(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 20) -> np.ndarray:
    tp = (high + low + close) / 3.0
    ma = np.convolve(tp, np.ones(period), 'same') / period
    md = np.array([np.mean(np.abs(tp[max(0, i - period // 2):min(len(tp), i + period // 2 + 1)] - ma[i])) for i in range(len(tp))])
    cci = (tp - ma) / np.maximum(1e-12, 0.015 * md)
    return cci


def ta_ichimoku(high: np.ndarray, low: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    # Tenkan (9), Kijun (26), Senkou A (deslocado, mas aqui só o valor atual)
    def _mid(h, l, p):
        return (np.array([np.max(h[max(0, i - p + 1):i + 1]) for i in range(len(h))]) +
                np.array([np.min(l[max(0, i - p + 1):i + 1]) for i in range(len(l))])) / 2.0

    tenkan = _mid(high, low, 9)
    kijun = _mid(high, low, 26)
    senkou_a = (tenkan + kijun) / 2.0  # (Senkou B exigiria período 52 + shift)
    return tenkan, kijun, senkou_a


def ta_obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    # Volume pode não existir, então criamos um volume "proxy" = 1
    if volume is None:
        volume = np.ones_like(close)
    sign = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(sign * volume)
    return obv


def ta_mfi(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray, period: int = 14) -> np.ndarray:
    if volume is None:
        volume = np.ones_like(close)
    tp = (high + low + close) / 3.0
    raw = tp * volume
    pos = np.where(tp > np.roll(tp, 1), raw, 0.0)
    neg = np.where(tp < np.roll(tp, 1), raw, 0.0)
    pos[0] = neg[0] = 0.0
    pos_sum = np.array([np.sum(pos[max(0, i - period + 1):i + 1]) for i in range(len(pos))])
    neg_sum = np.array([np.sum(neg[max(0, i - period + 1):i + 1]) for i in range(len(neg))])
    mfr = pos_sum / np.maximum(1e-12, neg_sum)
    mfi = 100.0 - (100.0 / (1.0 + mfr))
    return mfi


def ta_willr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    highest = np.array([np.max(high[max(0, i - period + 1):i + 1]) for i in range(len(high))])
    lowest = np.array([np.min(low[max(0, i - period + 1):i + 1]) for i in range(len(low))])
    willr = -100.0 * (highest - close) / np.maximum(1e-12, (highest - lowest))
    return willr


# ==========================
# Normalização de indicadores -> score 0..1
# ==========================
def _z01(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (x - lo) / max(1e-12, (hi - lo))))


def _score_from_indicators(data: Dict[str, Any]) -> float:
    """
    Converte indicadores "atuais" (última barra) em [0..1] com base em regrinhas simples.
    """
    s = 0.0
    w = 0.0

    # RSI (70 sobrecompra, 30 sobrevenda) – dá score alto se em tendência “saudável” (40~60)
    rsi = data["rsi"]
    rsi_score = 1.0 - abs((rsi - 50.0) / 50.0)  # pico em 50
    s += rsi_score * W_RSI; w += W_RSI

    # MACD: hist > 0 é bom; normaliza pela amplitude recente
    hist = data["macd_hist"]
    macd_score = _z01(hist, data.get("hist_min", -1.0), data.get("hist_max", 1.0))
    s += macd_score * W_MACD; w += W_MACD

    # EMAs: ema20 > ema50 e preço acima da ema20
    ema_ok = 0.0
    if data["ema20"] > data["ema50"] and data["close"] > data["ema20"]:
        ema_ok = 1.0
    elif data["ema20"] > data["ema50"] or data["close"] > data["ema20"]:
        ema_ok = 0.6
    s += ema_ok * W_EMA; w += W_EMA

    # Bollinger: se o preço está próximo da banda média e não “espremido”
    bb_mid = data["bb_mid"]
    bb_hi = data["bb_hi"]
    bb_lo = data["bb_lo"]
    if (bb_hi is not None) and (bb_lo is not None) and (bb_mid is not None):
        width = (bb_hi - bb_lo) / max(1e-12, bb_mid)
        mid_dist = 1.0 - min(1.0, abs(data["close"] - bb_mid) / max(1e-12, (bb_hi - bb_lo)))
        bb_score = 0.5 * _z01(width, 0.02, 0.12) + 0.5 * mid_dist
    else:
        bb_score = 0.5
    s += bb_score * W_BB; w += W_BB

    # StochRSI
    if EN_STOCHRSI:
        stK = data["stochK"]; stD = data["stochD"]
        st_score = 1.0 - abs(stK - 0.5) * 2.0  # pico em 0.5
        st_score = 0.5 * st_score + 0.5 * (1.0 - abs(stD - 0.5) * 2.0)
        s += st_score * W_STOCHRSI; w += W_STOCHRSI

    # ADX / DMI
    if EN_ADX:
        adx = data["adx"]; pdi = data["pdi"]; mdi = data["mdi"]
        trend = _z01(adx, 15, 35)
        dir_ok = 1.0 if pdi > mdi else 0.4
        adx_score = 0.6 * trend + 0.4 * dir_ok
        s += adx_score * W_ADX; w += W_ADX

    # ATR (volatilidade moderada é melhor) -> normaliza ATR/close
    if EN_ATR and data.get("atr_rel") is not None:
        atr_rel = data["atr_rel"]
        # bom entre 1% e 5%
        atr_score = 1.0 - abs(_z01(atr_rel, 0.01, 0.05) - 0.5) * 2.0
        s += atr_score * W_ATR; w += W_ATR

    # CCI (bom perto de 0, penaliza extremos)
    if EN_CCI:
        cci = data["cci"]
        cci_score = 1.0 - min(1.0, abs(cci) / 200.0)
        s += cci_score * W_CCI; w += W_CCI

    # Ichimoku: preço acima da nuvem (senkou_a ~ proxy) e Tenkan > Kijun
    if EN_ICHI:
        ichi_ok = 0.0
        if (data["close"] > data["senkou_a"]) and (data["tenkan"] >= data["kijun"]):
            ichi_ok = 1.0
        elif (data["close"] > data["senkou_a"]) or (data["tenkan"] >= data["kijun"]):
            ichi_ok = 0.6
        s += ichi_ok * W_ICHI; w += W_ICHI

    # OBV: direção do OBV nas últimas N barras (proxy de confirmação)
    if EN_OBV and (data.get("obv_slope") is not None):
        obv_score = _z01(data["obv_slope"], -1.0, 1.0)
        s += obv_score * W_OBV; w += W_OBV

    # MFI: zona média é saudável (40~60)
    if EN_MFI and (data.get("mfi") is not None):
        mfi = data["mfi"]
        mfi_score = 1.0 - abs((mfi - 50.0) / 50.0)
        s += mfi_score * W_MFI; w += W_MFI

    # Williams %R: próximo de -50 é “ok”
    if EN_WILLR and (data.get("willr") is not None):
        wr = data["willr"]  # -100..0
        wr_score = 1.0 - abs((wr + 50.0) / 50.0)
        s += wr_score * W_WILLR; w += W_WILLR

    if w <= 1e-9:
        return 0.0
    return max(0.0, min(1.0, s / w))


# ==========================
# Score + Detalhes
# ==========================
def score_signal(ohlc: List[Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Retorna (score_0_1, details_dict) e imprime bloco [IND] se DEBUG_INDICATORS.
    """
    t, o, h, l, c = _to_arrays(ohlc)

    # Volume não está no dataset original → usa proxy
    volume = np.ones_like(c)

    rsi = ta_rsi(c, 14)
    macd, macd_sig, macd_hist = ta_macd(c, 12, 26, 9)
    ema20 = _ema(c, 20)
    ema50 = _ema(c, 50)
    bb_mid, bb_hi, bb_lo = ta_bb(c, 20, 2.0)

    stochK = stochD = np.full_like(c, np.nan)
    if EN_STOCHRSI:
        stochK, stochD = ta_stochrsi(c, 14, 14, 3, 3)

    pdi = mdi = adx = np.full_like(c, np.nan)
    if EN_ADX:
        pdi, mdi, adx = ta_dmi_adx(h, l, c, 14)

    atr_rel = None
    if EN_ATR:
        atr = ta_atr(h, l, c, 14)
        atr_rel = float(atr[-1] / max(1e-12, c[-1]))

    cci = None
    if EN_CCI:
        cci = float(ta_cci(h, l, c, 20)[-1])

    tenkan = kijun = senkou_a = np.full_like(c, np.nan)
    if EN_ICHI:
        tenkan, kijun, senkou_a = ta_ichimoku(h, l)

    obv_slope = None
    if EN_OBV:
        obv = ta_obv(c, volume)
        # slope nos últimos 10 períodos (normalizado)
        if len(obv) >= 11:
            dy = obv[-1] - obv[-11]
            mx = np.max(np.abs(obv[-11:] - np.mean(obv[-11:])))
            obv_slope = float(dy / max(1e-9, mx))

    mfi_val = None
    if EN_MFI:
        mfi_val = float(ta_mfi(h, l, c, volume, 14)[-1])

    willr_val = None
    if EN_WILLR:
        willr_val = float(ta_willr(h, l, c, 14)[-1])

    # faixa dinâmica do hist para normalização
    hist_min = float(np.nanmin(macd_hist[-60:])) if len(macd_hist) >= 60 else float(np.nanmin(macd_hist))
    hist_max = float(np.nanmax(macd_hist[-60:])) if len(macd_hist) >= 60 else float(np.nanmax(macd_hist))

    details = {
        "close": float(c[-1]),
        "rsi": float(rsi[-1]),
        "macd": float(macd[-1]),
        "macd_sig": float(macd_sig[-1]),
        "macd_hist": float(macd_hist[-1]),
        "hist_min": hist_min,
        "hist_max": hist_max,
        "ema20": float(ema20[-1]),
        "ema50": float(ema50[-1]),
        "bb_mid": float(bb_mid[-1]) if not np.isnan(bb_mid[-1]) else None,
        "bb_hi": float(bb_hi[-1]) if not np.isnan(bb_hi[-1]) else None,
        "bb_lo": float(bb_lo[-1]) if not np.isnan(bb_lo[-1]) else None,
        "stochK": float(stochK[-1]) if EN_STOCHRSI and not np.isnan(stochK[-1]) else None,
        "stochD": float(stochD[-1]) if EN_STOCHRSI and not np.isnan(stochD[-1]) else None,
        "pdi": float(pdi[-1]) if EN_ADX and not np.isnan(pdi[-1]) else 0.0,
        "mdi": float(mdi[-1]) if EN_ADX and not np.isnan(mdi[-1]) else 0.0,
        "adx": float(adx[-1]) if EN_ADX and not np.isnan(adx[-1]) else 0.0,
        "atr_rel": atr_rel,
        "cci": cci,
        "tenkan": float(tenkan[-1]) if EN_ICHI and not np.isnan(tenkan[-1]) else 0.0,
        "kijun": float(kijun[-1]) if EN_ICHI and not np.isnan(kijun[-1]) else 0.0,
        "senkou_a": float(senkou_a[-1]) if EN_ICHI and not np.isnan(senkou_a[-1]) else 0.0,
        "obv_slope": obv_slope,
        "mfi": mfi_val,
        "willr": willr_val,
    }

    score = _score_from_indicators(details)

    if DEBUG_INDICATORS:
        def _fmt(x):
            return "None" if x is None else (f"{x:.2f}" if isinstance(x, (int, float)) else str(x))
        print(
            "[IND] "
            f"close={_fmt(details['close'])} | "
            f"rsi={_fmt(details['rsi'])} | "
            f"macd={_fmt(details['macd']):s}>{_fmt(details['macd_sig'])} "
            f"hist={_fmt(details['macd_hist'])} | "
            f"ema20={_fmt(details['ema20'])} ema50={_fmt(details['ema50'])} | "
            f"bb_mid={_fmt(details['bb_mid'])} bb_hi={_fmt(details['bb_hi'])} | "
            f"stochK={_fmt(details['stochK'])} stochD={_fmt(details['stochD'])} | "
            f"adx={_fmt(details['adx'])} pdi={_fmt(details['pdi'])} mdi={_fmt(details['mdi'])} | "
            f"atr_rel={_fmt(details['atr_rel'])} | "
            f"cci={_fmt(details['cci'])} | "
            f"ichiT={_fmt(details['tenkan'])} kijun={_fmt(details['kijun'])} sa={_fmt(details['senkou_a'])} | "
            f"obv_slope={_fmt(details['obv_slope'])} | "
            f"mfi={_fmt(details['mfi'])} | "
            f"willr={_fmt(details['willr'])} | "
            f"score={score*100:.1f}%"
        )

    return score, details


# ==========================
# Geração do Plano (entry/tp/sl)
# ==========================
def generate_signal(ohlc: List[Any]) -> Dict[str, Any]:
    """
    Gera um plano simples de trade a partir da última barra:
      - entry = close
      - tp/sl baseados em ATR (se disponível) ou largura de BB
      - rr padrão 2.0
    """
    _, _, h, l, c = _to_arrays(ohlc)
    close = float(c[-1])

    # Volatilidade de referência
    atr_val = None
    if EN_ATR:
        atr = ta_atr(h, l, c, 14)
        atr_val = float(atr[-1])

    if atr_val is None or atr_val <= 0:
        bb_mid, bb_hi, bb_lo = ta_bb(c, 20, 2.0)
        if not np.isnan(bb_hi[-1]) and not np.isnan(bb_lo[-1]):
            atr_val = float((bb_hi[-1] - bb_lo[-1]) / 4.0)  # proxy
        else:
            atr_val = close * 0.01  # fallback 1%

    rr = 2.0
    sl = close - atr_val
    tp = close + rr * atr_val

    return {
        "entry": close,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "strategy": "RSI+MACD+EMA+BB+STOCHRSI+ADX+CCI+ICHI+OBV+MFI+WILLR",
        "created_at": _ts(),
        "id": f"sig-{int(time.time())}",
    }
