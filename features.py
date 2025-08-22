# features.py
import pandas as pd
import numpy as np

def add_basic_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ret_1"] = df["close"].pct_change(1)
    df["ret_3"] = df["close"].pct_change(3)
    df["ret_5"] = df["close"].pct_change(5)

    df["vol_10"] = df["volume"].rolling(10).mean()
    df["vol_20"] = df["volume"].rolling(20).mean()

    sma5 = df["close"].rolling(5).mean()
    sma10 = df["close"].rolling(10).mean()
    sma20 = df["close"].rolling(20).mean()
    df["sma_5_over_20"] = sma5 / sma20
    df["sma_10_over_20"] = sma10 / sma20

    rng = (df["high"] - df["low"]).replace(0, np.nan)
    body = (df["close"] - df["open"]).abs()
    df["body_over_range"] = body / rng

    return df

def make_target(df: pd.DataFrame, horizon=1, threshold=0.002) -> pd.Series:
    """
    Alvo binário: 1 se retorno futuro (horizon) > threshold.
    """
    fut_ret = df["close"].shift(-horizon) / df["close"] - 1.0
    y = (fut_ret > threshold).astype(int)
    return y

FEATURE_COLS = ["ret_1","ret_3","ret_5","vol_10","vol_20",
                "sma_5_over_20","sma_10_over_20","body_over_range","volume"]
