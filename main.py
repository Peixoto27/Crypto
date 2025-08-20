# -*- coding: utf-8 -*-
"""
main.py
Loop principal:
  1) Seleciona universo
  2) Baixa OHLC (Binance -> fallback CoinGecko)
  3) Salva data_raw.json
  4) Calcula score técnico + sentimento (news/twitter)
  5) Loga e, se atingir threshold, emite sinais (opcional)

Compatível com:
- history_manager.save_ohlc_symbol / load_ohlc_symbol (cache por símbolo)
- sentiment_analyzer.get_sentiment_for_symbol(symbol, lookback_hours, …)
- apply_strategies.score_signal(ohlc_rows)  # retorna float ou dict/tuple
"""

from __future__ import annotations

import os
import json
import time
from math import ceil
from typing import Any, Dict, List, Tuple

# =========================
# Helpers
# =========================

def _env(name: str, default: str) -> str:
    v = os.getenv(name)
    return default if v is None else v

def _as_bool(v: str) -> bool:
    return str(v).lower().strip() in ("1", "true", "yes", "y", "t")

def _norm_ohlc(rows: Any) -> List[Dict[str, float]]:
    """
    Aceita:
      - [[ts,o,h,l,c], ...]
      - [{"t":..., "o":..., "h":..., "l":..., "c":...}, ...]
      - [{"open":..., "high":..., "low":..., "close":..., "t":...}, ...]
    Retorna lista de dicts padronizada: [{t,o,h,l,c}, ...]
    """
    out: List[Dict[str, float]] = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4])})
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for r in rows:
            t = float(r.get("t", r.get("time", r.get("timestamp", 0.0))))
            o = float(r.get("o", r.get("open", 0.0)))
            h = float(r.get("h", r.get("high", 0.0)))
            l = float(r.get("l", r.get("low", 0.0)))
            c = float(r.get("c", r.get("close", 0.0)))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
        return out
    return out

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if v > 1.0 and v <= 100.0:
            # muitos módulos retornam percentual 0..100
            return v / 100.0
        return v
    except Exception:
        return default

# =========================
# Config / ENV
# =========================

INTERVAL_MINUTES = int(_env("INTERVAL_MINUTES", "20"))

TOP_SYMBOLS = int(_env("TOP_SYMBOLS", "100"))
SYMBOLS_RAW = _env("SYMBOLS", "")  # lista fixa opcional "BTCUSDT,ETHUSDT,..."
SYMBOLS = [s.strip() for s in SYMBOLS_RAW.split(",") if s.strip()]

DAYS_OHLC = int(_env("DAYS_OHLC", "30"))
MIN_BARS  = int(_env("MIN_BARS", "180"))   # 30 dias de 4h ≈ 180 velas

WEIGHT_TECH = _safe_float(_env("WEIGHT_TECH", "1.0"), 1.0)
WEIGHT_SENT = _safe_float(_env("WEIGHT_SENT", "0.5"), 0.5)

SCORE_THRESHOLD = _safe_float(_env("SCORE_THRESHOLD", "0.70"), 0.70)
DEBUG_INDICATORS = _as_bool(_env("DEBUG_INDICATORS", "false"))

SAVE_HISTORY = _as_bool(_env("SAVE_HISTORY", "true"))
HISTORY_DIR  = _env("HISTORY_DIR", "data/history")
DATA_RAW_FILE = _env("DATA_RAW_FILE", "data_raw.json")

NEWS_USE    = _as_bool(_env("NEWS_USE", "true"))
TWITTER_USE = _as_bool(_env("TWITTER_USE", "true"))

LOOKBACK_HOURS_NEWS = int(_env("NEWS_LOOKBACK_HOURS", "12"))
LOOKBACK_HOURS_TW   = int(_env("TWITTER_LOOKBACK_MIN", "120"))

MAX_SYMBOLS_PER_CYCLE = int(_env("MAX_SYMBOLS_PER_CYCLE", "30"))
SLEEP_BETWEEN_CALLS   = float(_env("SLEEP_BETWEEN_CALLS", "5"))

# =========================
# Imports dos módulos do projeto
# =========================

# Fontes de OHLC
_fetch_ohlc_primary = None   # Binance
_fetch_ohlc_fallback = None  # CoinGecko

try:
    from data_fetcher_binance import fetch_ohlc as _fetch_ohlc_primary
except Exception:
    _fetch_ohlc_primary = None

try:
    from data_fetcher_coingecko import fetch_ohlc as _fetch_ohlc_fallback
except Exception:
    _fetch_ohlc_fallback = None

# História (cache por símbolo) — opcional
try:
    from history_manager import save_ohlc_symbol, load_ohlc_symbol
except Exception:
    def save_ohlc_symbol(*_a, **_k):  # type: ignore
        return None
    def load_ohlc_symbol(*_a, **_k):  # type: ignore
        return None

# Técnicos
try:
    # Usamos score_signal como agregador (já existente no seu projeto)
    from apply_strategies import score_signal as score_from_indicators
except Exception:
    def score_from_indicators(_rows: List[Dict[str, float]]) -> float:
        return 0.0

# Sentimento
try:
    from sentiment_analyzer import get_sentiment_for_symbol
except Exception:
    def get_sentiment_for_symbol(symbol: str, lookback_hours: int = 12, **_k):
        # retorna "neutro" caso módulo não esteja disponível
        return {"score": 0.5, "n_news": 0, "n_tweets": 0}


# =========================
# Universo
# =========================

def _fetch_top_symbols(n: int) -> List[str]:
    # Caso você já tenha um método próprio, importe-o aqui.
    # Como fallback, usamos algumas majors.
    base = [
        "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT",
        "ADAUSDT","DOGEUSDT","TRXUSDT","AVAXUSDT","LINKUSDT",
    ]
    return base[:n]


def pick_universe() -> List[str]:
    if SYMBOLS:
        return SYMBOLS[:MAX_SYMBOLS_PER_CYCLE]
    uni = _fetch_top_symbols(TOP_SYMBOLS)
    # Remove pares estáveis redundantes (ex.: FDUSDUSDT)
    uni = [s for s in uni if not s.endswith("FDUSDT") and not s.endswith("FDUSDUSDT")]
    return uni[:MAX_SYMBOLS_PER_CYCLE]


# =========================
# OHLC com retry e fallback
# =========================

def fetch_ohlc_with_retry(symbol: str, days: int) -> List[Dict[str, float]]:
    # 1) tenta cache
    if SAVE_HISTORY:
        cached = load_ohlc_symbol(HISTORY_DIR, symbol)
        norm = _norm_ohlc(cached)
        if len(norm) >= MIN_BARS:
            return norm

    # 2) tenta fonte primária (Binance)
    if _fetch_ohlc_primary:
        try:
            raw = _fetch_ohlc_primary(symbol, days=days)
            bars = _norm_ohlc(raw)
            if len(bars) >= MIN_BARS:
                if SAVE_HISTORY:
                    save_ohlc_symbol(HISTORY_DIR, symbol, raw)
                return bars
        except Exception as e:
            print(f"⚠️ Binance falhou {symbol}: {e}")

    # 3) fallback CoinGecko
    if _fetch_ohlc_fallback:
        try:
            raw = _fetch_ohlc_fallback(symbol, days=days)
            bars = _norm_ohlc(raw)
            if len(bars) >= MIN_BARS:
                if SAVE_HISTORY:
                    save_ohlc_symbol(HISTORY_DIR, symbol, raw)
                return bars
        except Exception as e:
            print(f"⚠️ CoinGecko falhou {symbol}: {e}")

    return []


# =========================
# Sentimento seguro (aceita dict|tuple|float)
# =========================

def _safe_sentiment(symbol: str) -> Dict[str, Any]:
    try:
        res = get_sentiment_for_symbol(
            symbol,
            lookback_hours=max(LOOKBACK_HOURS_NEWS, LOOKBACK_HOURS_TW),
        )
        # Pode vir dict, tuple ou float
        if isinstance(res, dict):
            score = _safe_float(res.get("score", 0.5), 0.5)
            n_news = int(res.get("n_news", res.get("n", 0)))
            n_tw   = int(res.get("n_tweets", res.get("tw", 0)))
        elif isinstance(res, tuple):
            # Ex.: (0.56, {"n_news": 3, "n_tweets": 5})
            score = _safe_float(res[0], 0.5)
            meta  = res[1] if len(res) > 1 and isinstance(res[1], dict) else {}
            n_news = int(meta.get("n_news", meta.get("n", 0)))
            n_tw   = int(meta.get("n_tweets", meta.get("tw", 0)))
        else:
            score = _safe_float(res, 0.5)
            n_news = 0
            n_tw = 0

        # Caso News/Twitter estejam desligados por ENV, neutraliza contagens
        if not NEWS_USE:
            n_news = 0
        if not TWITTER_USE:
            n_tw = 0

        # Se ambos estão desligados, força neutro
        if not NEWS_USE and not TWITTER_USE:
            score = 0.5

        return {"score": max(0.0, min(1.0, score)), "n_news": n_news, "n_tweets": n_tw}
    except Exception as e:
        print(f"[SENT] erro {symbol}: {e}")
        return {"score": 0.5, "n_news": 0, "n_tweets": 0}


# =========================
# Técnico seguro
# =========================

def _safe_tech_score(bars: List[Dict[str, float]]) -> float:
    try:
        sc = score_from_indicators(bars)
        if isinstance(sc, dict):
            sc = sc.get("score", sc.get("value", 0.0))
        elif isinstance(sc, tuple):
            sc = sc[0]
        return max(0.0, min(1.0, _safe_float(sc, 0.0)))
    except Exception as e:
        if DEBUG_INDICATORS:
            print(f"[IND] erro em score_signal: {e}")
        return 0.0


# =========================
# Pipeline
# =========================

def run_pipeline() -> None:
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MINUTES:.1f} min.")
    print(f"🔎 NEWS ativo?: {NEWS_USE} | IA ativa?: {_env('IA_USE','true')} | Histórico ativado?: {SAVE_HISTORY} | Twitter ativo?: {TWITTER_USE}")

    universe = pick_universe()
    # log extras: remoção de pares estáveis
    stables = [s for s in universe if s.endswith("FDUSDT") or s.endswith("FDUSDUSDT")]
    if stables:
        print(f"🧠 Removidos {len(stables)} pares estáveis redundantes (ex.: FDUSDUSDT).")
        universe = [s for s in universe if s not in stables]

    print(f"🧪 Moedas deste ciclo ({min(len(universe), MAX_SYMBOLS_PER_CYCLE)}/{TOP_SYMBOLS}): {', '.join(universe[:MAX_SYMBOLS_PER_CYCLE])}")

    # 1) coleta OHLC
    collected: Dict[str, List[List[float]]] = {}
    for sym in universe:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        bars = fetch_ohlc_with_retry(sym, DAYS_OHLC)
        if len(bars) < MIN_BARS:
            print(f"⚠️ {sym}: OHLC insuficiente ({len(bars)}/{MIN_BARS})")
            continue
        print(f"   → OK | candles= {len(bars)}")
        # Para salvar em data_raw.json usamos lista de listas
        collected[sym] = [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in bars]
        time.sleep(SLEEP_BETWEEN_CALLS)

    # 2) salva data_raw.json
    with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
        json.dump({"symbols": list(collected.keys()), "data": collected}, f, ensure_ascii=False)
    print(f"💾 Salvo {DATA_RAW_FILE} ({len(collected)} ativos)")

    # 3) scoring + logs
    for sym, rows in collected.items():
        bars = _norm_ohlc(rows)
        last_price = bars[-1]["c"] if bars else 0.0

        tech = _safe_tech_score(bars)
        sent_info = _safe_sentiment(sym)
        sent = float(sent_info["score"])
        n_news = sent_info.get("n_news", 0)
        n_tw   = sent_info.get("n_tweets", 0)

        # mistura ponderada
        wsum = max(1e-9, WEIGHT_TECH + WEIGHT_SENT)
        mix  = (WEIGHT_TECH * tech + WEIGHT_SENT * sent) / wsum

        if DEBUG_INDICATORS:
            # dump compacto do último estado dos indicadores (seu agregador pode já printar internamente)
            print(f"[IND] close={last_price:.8g} | score={tech*100:.1f}%")

        print(
            f"[IND] {sym} | Técnico: {tech*100:.1f}% | "
            f"Sentimento: {sent*100:.1f}% (news n={n_news}, tw n={n_tw}) | "
            f"Mix(T:{WEIGHT_TECH:.1f},S:{WEIGHT_SENT:.1f}): {mix*100:.1f}% (min {SCORE_THRESHOLD*100:.0f}%)"
        )

        # Aqui você pode chamar seu generator de sinais (se já existir)
        # Exemplo:
        # if mix >= SCORE_THRESHOLD:
        #     sig = generate_signal(bars)  # se você já tiver isso pronto
        #     notifier_v2.notify_new_signal(sig)

    print(f"🕒 Fim: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")


# Execução direta local
if __name__ == "__main__":
    run_pipeline()
