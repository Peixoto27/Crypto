# -*- coding: utf-8 -*-
"""
main.py — pipeline robusto (coleta OHLC, técnico, sentimento e mix)

- Compatível com sentiment_analyzer.get_sentiment_for_symbol() que retorna dict,
  mas lida também com tupla (score, n) para não quebrar.
- Logs amigáveis iguais ao seu formato.
- Salva data_raw.json.
"""

from __future__ import annotations

import os
import json
import time
from math import ceil
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

# =========================
# Helpers de ambiente
# =========================
def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "y", "on")

def _f(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

def _i(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    if v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# =========================
# Config do runner
# =========================
INTERVAL_MIN      = _f("INTERVAL_MIN", 20.0)      # só para log
DAYS_OHLC         = _i("DAYS_OHLC", 30)
MIN_BARS          = _i("MIN_BARS", 180)
SYMBOLS_ENV       = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
CACHE_DATA_FILE   = os.getenv("DATA_RAW_FILE", "data_raw.json")

WEIGHT_TECH       = _f("WEIGHT_TECH", 1.0)
WEIGHT_SENT       = _f("WEIGHT_SENT", 1.0)

NEWS_USE          = _bool("NEWS_USE", True) or _bool("ENABLE_NEWS", True)
TW_USE            = _bool("TWITTER_USE", False)
WEIGHT_NEWS       = _f("WEIGHT_NEWS", _f("NEWS_WEIGHT", 1.0))
WEIGHT_TW         = _f("WEIGHT_TW",   _f("TWITTER_WEIGHT", 1.0))

SAVE_HISTORY      = _bool("SAVE_HISTORY", True)
HISTORY_DIR       = os.getenv("HISTORY_DIR", "data/history")

# =========================
# Import de módulos do projeto (com fallback)
# =========================
# OHLC (CoinGecko)
fetch_ohlc = None
fetch_top_symbols = None
try:
    from data_fetcher_coingecko import fetch_ohlc as _fo, fetch_top_symbols as _fts
    fetch_ohlc = _fo
    fetch_top_symbols = _fts
except Exception:
    pass

# Técnicos
_compute_indicators = None
_score_from_ind     = None
try:
    from apply_strategies import compute_indicators as _ci, score_from_indicators as _sfi
    _compute_indicators = _ci
    _score_from_ind     = _sfi
except Exception:
    pass

# Sentimento unificado (News + Twitter)
try:
    from sentiment_analyzer import get_sentiment_for_symbol as _get_sentiment  # 
except Exception:
    _get_sentiment = None

# =========================
# Utilidades
# =========================
def _norm_rows(rows: Any) -> List[Dict[str, float]]:
    """
    Normaliza OHLC em lista de dicts [{t,o,h,l,c}, ...].
    Aceita [[ts,o,h,l,c], ...] ou [{open,high,low,close}, ...].
    """
    out: List[Dict[str, float]] = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4])})
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            o = float(r.get("open", r.get("o", 0.0)))
            h = float(r.get("high", r.get("h", 0.0)))
            l = float(r.get("low",  r.get("l", 0.0)))
            c = float(r.get("close",r.get("c", 0.0)))
            t = float(r.get("t", 0.0))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out

def _save_json(path: str, obj: Any):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# =========================
# Pipeline
# =========================
def _collect_universe() -> List[str]:
    if SYMBOLS_ENV:
        base = SYMBOLS_ENV[:]
    else:
        base = []
        try:
            if fetch_top_symbols:
                base = fetch_top_symbols(100)
        except Exception:
            base = []
    # remover pares estáveis redundantes
    stables = {"FDUSDUSDT", "USDCUSDT", "USDTBRL", "BUSDUSDT"}
    rem = [s for s in base if s.upper() in stables]
    if rem:
        print(f"🧠 Removidos {len(rem)} pares estáveis redundantes (ex.: {rem[0]}).")
    return [s for s in base if s.upper() not in stables]

def _fetch_all_ohlc(symbols: List[str]) -> Dict[str, List[Dict[str, float]]]:
    out: Dict[str, List[Dict[str, float]]] = {}
    for s in symbols:
        try:
            print(f"📊 Coletando OHLC {s} (days={DAYS_OHLC})…")
            rows = []
            if fetch_ohlc:
                rows = fetch_ohlc(s, DAYS_OHLC)
            bars = _norm_rows(rows)
            if len(bars) < MIN_BARS:
                print(f"⚠️ {s}: OHLC insuficiente ({len(bars)}/{MIN_BARS})")
                continue
            out[s] = bars
            print("   → OK | candles={}".format(len(bars)))
        except Exception as e:
            print(f"⚠️ Erro OHLC {s}: {e}")
    return out

def _technical_score(bars: List[Dict[str, float]]) -> Tuple[float, Dict[str, float]]:
    """
    Calcula o score técnico (0..1) e retorna (score, features) para log.
    Usa apply_strategies se existir; caso contrário, devolve 0.0.
    """
    if not bars or len(bars) < 5:
        return (0.0, {})
    if _compute_indicators and _score_from_ind:
        try:
            feats = _compute_indicators(bars)
            score = _score_from_ind(feats)
            # normaliza
            if isinstance(score, dict):
                s = float(score.get("score", 0.0))
            elif isinstance(score, tuple):
                s = float(score[0])
            else:
                s = float(score)
            if s > 1.0:
                s /= 100.0
            s = max(0.0, min(1.0, s))
            return (s, feats or {})
        except Exception as e:
            print(f"[IND] erro em score_signal: {e}")
            return (0.0, {})
    return (0.0, {})

def _sentiment_mix(symbol: str) -> Dict[str, Any]:
    """
    Garante dict com: score, parts{news, twitter}, counts{news, twitter}, enabled, weights
    """
    if not _get_sentiment:
        # neutro
        return {
            "score": 0.5,
            "parts": {"news": 0.5, "twitter": 0.5},
            "counts": {"news": 0, "twitter": 0},
            "enabled": {"news": NEWS_USE, "twitter": TW_USE},
            "weights": {"news": WEIGHT_NEWS, "twitter": WEIGHT_TW},
        }
    try:
        res = _get_sentiment(symbol)
        # Aceita tupla ou dict
        if isinstance(res, tuple):
            sc = float(res[0])
            n  = int(res[1]) if len(res) > 1 else 0
            if sc > 1.0: sc /= 100.0
            sc = max(0.0, min(1.0, sc))
            return {
                "score": sc,
                "parts": {"news": sc, "twitter": 0.5},
                "counts": {"news": n, "twitter": 0},
                "enabled": {"news": NEWS_USE, "twitter": TW_USE},
                "weights": {"news": WEIGHT_NEWS, "twitter": WEIGHT_TW},
            }
        elif isinstance(res, dict):
            sc = float(res.get("score", 0.5))
            if sc > 1.0: sc /= 100.0
            res["score"] = max(0.0, min(1.0, sc))
            return res
        else:
            sc = float(res)
            if sc > 1.0: sc /= 100.0
            return {
                "score": max(0.0, min(1.0, sc)),
                "parts": {"news": sc, "twitter": 0.5},
                "counts": {"news": 0, "twitter": 0},
                "enabled": {"news": NEWS_USE, "twitter": TW_USE},
                "weights": {"news": WEIGHT_NEWS, "twitter": WEIGHT_TW},
            }
    except Exception as e:
        print(f"[SENT] erro ao obter sentimento {symbol}: {e}")
        return {
            "score": 0.5,
            "parts": {"news": 0.5, "twitter": 0.5},
            "counts": {"news": 0, "twitter": 0},
            "enabled": {"news": NEWS_USE, "twitter": TW_USE},
            "weights": {"news": WEIGHT_NEWS, "twitter": WEIGHT_TW},
        }

def _mix_scores(tech: float, sent: float) -> float:
    wsum = WEIGHT_TECH + WEIGHT_SENT
    if wsum <= 0:
        return 0.5
    return max(0.0, min(1.0, (tech * WEIGHT_TECH + sent * WEIGHT_SENT) / wsum))

# =========================
# Função principal
# =========================
def run_pipeline():
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")
    print(f"🔎 NEWS ativo?: {NEWS_USE} | IA ativa?: {True} | Histórico ativado?: {SAVE_HISTORY} | Twitter ativo?: {TW_USE}")
    print(f"   Pesos -> Tech={WEIGHT_TECH:.2f} | Sent={WEIGHT_SENT:.2f} | News={WEIGHT_NEWS:.2f} | Tw={WEIGHT_TW:.2f}")

    symbols = _collect_universe()
    if not symbols:
        print("🧪 Moedas deste ciclo (0/0): —")
        print("❌ Nenhum ativo disponível.")
        return

    # corta para bloco (8 por ciclo, como você usa)
    batch_n = _i("BATCH_SIZE", 8)
    symbols_batch = symbols[:batch_n]
    print(f"🧪 Moedas deste ciclo ({len(symbols_batch)}/{len(symbols)}): {', '.join(symbols_batch)}")

    # coleta OHLC
    data = _fetch_all_ohlc(symbols_batch)

    # salva raw
    _save_json(CACHE_DATA_FILE, {"symbols": list(data.keys()),
                                 "data": {k: [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in v] for k, v in data.items()}})
    print(f"💾 Salvo {CACHE_DATA_FILE} ({len(data)} ativos)")

    # loop de cálculo e logs
    for sym, bars in data.items():
        tech_score, feats = _technical_score(bars)
        sent_info = _sentiment_mix(sym)
        sent_score = float(sent_info.get("score", 0.5))

        news_n = sent_info.get("counts", {}).get("news", 0)
        tw_n   = sent_info.get("counts", {}).get("twitter", 0)

        mix = _mix_scores(tech_score, sent_score)

        # log detalhado dos indicadores principais (se existirem)
        close = bars[-1]["c"] if bars else None
        if feats:
            rsi   = feats.get("rsi")
            macd  = feats.get("macd")
            hist  = feats.get("macd_hist")
            ema20 = feats.get("ema20")
            ema50 = feats.get("ema50")
            bbm   = feats.get("bb_mid")
            bbh   = feats.get("bb_hi")
            stK   = feats.get("stochK")
            stD   = feats.get("stochD")
            adx   = feats.get("adx")
            pdi   = feats.get("+di") or feats.get("pdi")
            mdi   = feats.get("-di") or feats.get("mdi")
            cci   = feats.get("cci")
            ichiT = feats.get("ichi_tenkan") or feats.get("ichiT")
            kijun = feats.get("ichi_kijun")  or feats.get("kijun")
            obv_s = feats.get("obv_slope")
            mfi   = feats.get("mfi")
            willr = feats.get("willr")

            print(
                f"[IND] {sym} | close={close} | "
                f"rsi={_fmt(rsi)} | macd={_fmt(macd)} hist={_fmt(hist)} | "
                f"ema20={_fmt(ema20)} ema50={_fmt(ema50)} | "
                f"bb_mid={_fmt(bbm)} bb_hi={_fmt(bbh)} | "
                f"stochK={_fmt(stK)} stochD={_fmt(stD)} | "
                f"adx={_fmt(adx)} pdi={_fmt(pdi)} mdi={_fmt(mdi)} | "
                f"cci={_fmt(cci)} | ichiT={_fmt(ichiT)} kijun={_fmt(kijun)} | "
                f"obv_slope={_fmt(obv_s)} | mfi={_fmt(mfi)} | willr={_fmt(willr)} | "
                f"score={_pct(tech_score)}"
            )
        else:
            print(f"[IND] close={close} | score={_pct(tech_score)}")

        print(
            f"[IND] {sym} | Técnico: {_pct(tech_score)} | "
            f"Sentimento: {_pct(sent_score)} (news n={news_n}, tw n={tw_n}) | "
            f"Mix(T:{WEIGHT_TECH:.1f},S:{WEIGHT_SENT:.1f}): {_pct(mix)} (min 70%)"
        )

    print(f"🕒 Fim: {_ts()}")

def _fmt(v: Any) -> str:
    try:
        return f"{float(v):.2f}"
    except Exception:
        return "None"

def _pct(x: float) -> str:
    return f"{x*100:.1f}%"

# =========================
# Entry point
# =========================
if __name__ == "__main__":
    run_pipeline()
