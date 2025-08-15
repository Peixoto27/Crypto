# -*- coding: utf-8 -*-
"""
apply_strategies.py
- Normaliza OHLC para DataFrame (timestamp, open, high, low, close, volume)
- score_signal(ohlc) -> float (0..1)
- generate_signal(ohlc) -> dict com entry/tp/sl/rr/strategy/created_at
"""

from __future__ import annotations
from typing import Any, Dict, List, Sequence, Tuple, Union
import math
import time

import numpy as np
import pandas as pd


# =========================
# Utilidades de normalização
# =========================
def _to_df(
    ohlc: Union[
        List[List[float]],
        List[Dict[str, Any]],
        Dict[str, List[float]],
        pd.DataFrame,
    ]
) -> pd.DataFrame:
    """
    Converte vários formatos de OHLC em um DataFrame com colunas padrão:
    ['timestamp','open','high','low','close','volume']
    - Lista de listas: [[ts,o,h,l,c(,v?)], ...]
    - Lista de dicts:  [{'time':..,'open':..,'high':..,'low':..,'close':..,'volume':..}, ...]
    - Dict de listas:  {'timestamp':[], 'open':[], 'high':[], 'low':[], 'close':[], 'volume':[]}
    - DataFrame: tenta renomear colunas.
    Lança ValueError se não conseguir mapear.
    """
    # Caso já seja DF, apenas padroniza nomes
    if isinstance(ohlc, pd.DataFrame):
        df = ohlc.copy()
    elif isinstance(ohlc, list) and len(ohlc) > 0:
        first = ohlc[0]
        # lista de listas
        if isinstance(first, (list, tuple)):
            # 5 ou 6 colunas
            n = len(first)
            if n < 5:
                raise ValueError("Lista de listas com menos de 5 campos (ts,o,h,l,c).")
            # volume pode faltar
            cols = ["timestamp", "open", "high", "low", "close"] + (["volume"] if n >= 6 else [])
            df = pd.DataFrame(ohlc, columns=cols)
            if "volume" not in df.columns:
                df["volume"] = 0.0
        # lista de dicts
        elif isinstance(first, dict):
            df = pd.DataFrame(ohlc)
        else:
            raise ValueError("Formato de lista não reconhecido para OHLC.")
    elif isinstance(ohlc, dict):
        df = pd.DataFrame(ohlc)
    else:
        raise ValueError("Formato de OHLC não reconhecido.")

    # Tenta mapear nomes alternativos
    rename_map = {}
    lower_cols = {c.lower(): c for c in df.columns}
    def pick(*options, default=None):
        for opt in options:
            if opt in lower_cols:
                return lower_cols[opt]
        return default

    c_ts = pick("ts", "time", "timestamp", default=None)
    c_o  = pick("o", "open")
    c_h  = pick("h", "high")
    c_l  = pick("l", "low")
    c_c  = pick("c", "close", "closing")
    c_v  = pick("v", "vol", "volume", default=None)

    if c_ts is None:
        # cria timestamp sequencial se não veio
        df["timestamp"] = np.arange(len(df), dtype=np.int64)
    else:
        rename_map[c_ts] = "timestamp"

    # campos obrigatórios
    for src, dst in [(c_o, "open"), (c_h, "high"), (c_l, "low"), (c_c, "close")]:
        if src is None:
            raise ValueError("OHLC sem colunas open/high/low/close válidas.")
        rename_map[src] = dst

    if c_v is None:
        df["volume"] = 0.0
    else:
        rename_map[c_v] = "volume"

    if rename_map:
        df = df.rename(columns=rename_map)

    # ordena por tempo se possível
    if "timestamp" in df.columns:
        df = df.sort_values("timestamp").reset_index(drop=True)

    # garante tipo float
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # remove NaN
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

    if len(df) < 30:  # mínimo pra alguns indicadores
        raise ValueError("Poucos candles após normalização (<30).")

    return df


# =========================
# Indicadores
# =========================
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1/period, adjust=False).mean()
    roll_down = down.ewm(alpha=1/period, adjust=False).mean()
    rs = roll_up / (roll_down.replace(0, np.nan))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)

def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line
    return macd_line, signal_line, hist

def bollinger(series: pd.Series, period: int = 20, mult: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    ma = series.rolling(period).mean()
    sd = series.rolling(period).std(ddof=0)
    upper = ma + mult * sd
    lower = ma - mult * sd
    return lower, ma, upper

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean().fillna(method="bfill")


# =========================
# Scoring
# =========================
def _compute_score(df: pd.DataFrame) -> float:
    """
    Combina sinais de tendência/momentum/volatilidade numa nota 0..1.
    Heurística leve, estável e sem dependência externa.
    """
    close = df["close"]

    # Indicadores
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    r = rsi(close, 14)
    macd_line, signal_line, hist = macd(close)
    bb_low, bb_mid, bb_up = bollinger(close, 20, 2.0)

    last = -1  # última barra
    score_parts = []

    # Tendência de curto x médio
    trend_short = 1.0 if ema20.iloc[last] > ema50.iloc[last] else 0.0
    score_parts.append(trend_short)

    # Tendência x longo prazo
    trend_long = 1.0 if ema50.iloc[last] > ema200.iloc[last] else 0.0
    score_parts.append(trend_long)

    # Posição do preço na banda (quanto mais perto da banda superior, maior prob. de força)
    if pd.notna(bb_up.iloc[last]) and pd.notna(bb_low.iloc[last]):
        pos = (close.iloc[last] - bb_low.iloc[last]) / max(1e-9, (bb_up.iloc[last] - bb_low.iloc[last]))
        pos = float(np.clip(pos, 0.0, 1.0))
        score_parts.append(pos)
    else:
        score_parts.append(0.5)

    # RSI: 50-70 levemente positivo, >70 muito positivo; <30 negativo
    rsi_val = r.iloc[last]
    if rsi_val >= 70:
        score_rsi = 1.0
    elif rsi_val >= 50:
        score_rsi = 0.7
    elif rsi_val >= 30:
        score_rsi = 0.3
    else:
        score_rsi = 0.1
    score_parts.append(score_rsi)

    # MACD hist > 0 favorece alta
    macd_pos = 1.0 if hist.iloc[last] > 0 else 0.0
    score_parts.append(macd_pos)

    # Cruzes recentes (EMA20 cruzou EMA50 pra cima nas últimas n barras?)
    cross_up = 0.0
    for lookback in range(1, 6):
        if ema20.iloc[-lookback-1] < ema50.iloc[-lookback-1] and ema20.iloc[-lookback] > ema50.iloc[-lookback]:
            cross_up = 1.0
            break
    score_parts.append(cross_up)

    # Média dos componentes
    raw = float(np.mean(score_parts))
    return float(np.clip(raw, 0.0, 1.0))


def score_signal(ohlc: Any) -> float:
    """
    Entrada tolerante (lista/df/dict). Nunca levanta exceção:
    - Em caso de falha, retorna 0.0
    """
    try:
        df = _to_df(ohlc)
        return _compute_score(df)
    except Exception:
        return 0.0


# =========================
# Geração de sinal
# =========================
def generate_signal(ohlc: Any) -> Dict[str, Any] | None:
    """
    Retorna dict com:
      - entry: preço de entrada (close atual)
      - tp: take profit
      - sl: stop loss
      - rr: risk:reward (default 2.0)
      - strategy: string
      - created_at: ISO simples
    Em falha, retorna None.
    """
    try:
        df = _to_df(ohlc)
    except Exception:
        return None

    if len(df) < 30:
        return None

    close = df["close"]
    last_price = float(close.iloc[-1])

    # Volatilidade via ATR para montar SL/TP
    atr14 = atr(df, 14)
    vol = float(atr14.iloc[-1])
    if not math.isfinite(vol) or vol <= 0:
        # fallback usando desvio padrão de 20
        vol = float(close.pct_change().rolling(20).std().iloc[-1] * last_price)
        if not math.isfinite(vol) or vol <= 0:
            vol = last_price * 0.01  # 1% como fallback duro

    # Direcionalidade simples: relação EMA20 x EMA50 e MACD
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    macd_line, signal_line, hist = macd(close)

    long_bias = (ema20.iloc[-1] > ema50.iloc[-1]) and (hist.iloc[-1] > 0)

    rr = 2.0
    if long_bias:
        sl = last_price - 1.0 * vol
        tp = last_price + rr * (last_price - sl)
    else:
        # sinal de venda ainda não é suportado no seu fluxo atual (Telegram etc.)
        # então geramos só se for compra. Se preferir suportar short, remova este retorno.
        return None

    return {
        "entry": round(last_price, 8),
        "tp": round(tp, 8),
        "sl": round(sl, 8),
        "rr": rr,
        "strategy": "RSI+MACD+EMA+BB",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
    }
