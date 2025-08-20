# -*- coding: utf-8 -*-
"""
Pipeline principal – versão CryptoCompare-first
- Universo de moedas por CryptoCompare (ou lista fixa via UNIVERSE_LIST)
- OHLC 4h (30 dias) por CryptoCompare
- Sentimento via sentiment_analyzer (se existir) com fallback
- Score técnico: usa função externa do projeto se disponível; senão fallback simples
- Logs compatíveis com seu runner.py
"""

from __future__ import annotations
import os
import json
import time
import math
import traceback
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any, Optional

import requests

# =========================
# Config/Env (com defaults)
# =========================
INTERVAL_MIN = float(os.getenv("BINANCE_INTERVAL", os.getenv("INTERVAL_MIN", "20")))
DAYS_OHLC = int(os.getenv("DAYS_OHLC", "30"))
CANDLES_4H = DAYS_OHLC * 24 // 4  # 30d * 6 = 180
UNIVERSE_LIMIT = int(os.getenv("UNIVERSE_LIMIT", "100"))

# Feature flags (log)
NEWS_USE = os.getenv("NEWS_USE", "true").lower() == "true"
AI_ENABLE = os.getenv("AI_ENABLE", "true").lower() == "true"
HISTORY_ENABLE = os.getenv("HISTORY_ENABLE", "true").lower() == "true"
TWITTER_USE = os.getenv("TWITTER_USE", "true").lower() == "true"

# Pesos/mix
MIX_TECH_OVER_SENT = float(os.getenv("MIX_TECH_OVER_SENT", "1.5"))
MIX_SENT_OVER_TECH = float(os.getenv("MIX_SENT_OVER_TECH", "1.0"))
MIX_MIN_THRESHOLD = float(os.getenv("MIX_MIN_THRESHOLD", "70"))

# CryptoCompare
CC_API_KEY = os.getenv("CRYPTOCOMPARE_API_KEY", "").strip()
CC_BASE = "https://min-api.cryptocompare.com"

# Remoção de pares redundantes (FDUSD, TUSD etc.)
STABLE_TICKERS = set(os.getenv(
    "STABLE_TICKERS",
    "USDT,USDC,FDUSD,TUSD,DAI,EUR,BRL,TRY,BUSD,UST"
).split(","))


# =========================
# Util / Logs
# =========================
def _log(msg: str) -> None:
    print(msg, flush=True)


def _err(msg: str) -> None:
    print(f"\u274c {msg}", flush=True)


def _warn(msg: str) -> None:
    print(f"\u26a0\ufe0f {msg}", flush=True)


def _ok(msg: str) -> None:
    print(f"   \u2192 OK | {msg}", flush=True)


# =========================
# Universo de Moedas
# =========================
def _pair_from_symbol(base: str, quote: str = "USDT") -> str:
    return f"{base.upper()}{quote.upper()}"


def get_universe() -> List[str]:
    """
    1) Se UNIVERSE_LIST estiver definida -> usa.
    2) Senão, pega top mktcap pela CryptoCompare e monta pares XXXUSDT.
    """
    raw = os.getenv("UNIVERSE_LIST", "").strip()
    if raw:
        items = [s.strip().upper() for s in raw.split(",") if s.strip()]
        return items[:UNIVERSE_LIMIT]

    if not CC_API_KEY:
        _err("Sem CRYPTOCOMPARE_API_KEY. Defina-a ou use UNIVERSE_LIST.")
        return []

    # Top mktcap (máx 100 por chamada)
    url = f"{CC_BASE}/data/top/mktcapfull"
    params = {"tsym": "USD", "limit": min(UNIVERSE_LIMIT, 100), "api_key": CC_API_KEY}
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        result = []
        for item in data.get("Data", []):
            coin = item.get("CoinInfo", {})
            sym = (coin.get("Name") or "").upper()
            if not sym:
                continue
            # evita pares com a própria stable como base (ex.: USDTUSDT)
            if sym in STABLE_TICKERS:
                continue
            result.append(_pair_from_symbol(sym, "USDT"))
        # Remove pares estáveis redundantes (ex.: FDUSDUSDT)
        before = len(result)
        result = [p for p in result if p.replace("USDT", "") not in STABLE_TICKERS]
        removed = before - len(result)
        if removed > 0:
            _log(f"\U0001f9e0 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")
        return result
    except Exception as e:
        _err(f"Falha ao obter universo na CryptoCompare: {e}")
        return []


# =========================
# OHLC (CryptoCompare, 4h)
# =========================
def fetch_ohlc_cc(pair: str, candles: int = CANDLES_4H) -> List[Dict[str, Any]]:
    """
    Busca candles de 4h na CryptoCompare: histohour + aggregate=4
    Retorna lista de dicts: [{t, o, h, l, c}, ...]
    """
    if not CC_API_KEY:
        raise RuntimeError("CRYPTOCOMPARE_API_KEY não configurada.")
    base = pair.replace("USDT", "")
    url = f"{CC_BASE}/data/v2/histohour"
    params = {
        "fsym": base,
        "tsym": "USDT",
        "limit": max(1, min(candles, 2000)) - 1,  # limit é n-1
        "aggregate": 4,
        "api_key": CC_API_KEY,
    }
    r = requests.get(url, params=params, timeout=25)
    r.raise_for_status()
    js = r.json()
    if js.get("Response") != "Success":
        raise RuntimeError(f"CC retornou erro: {js.get('Message')}")
    data = js.get("Data", {}).get("Data", [])
    ohlc = []
    for d in data:
        ohlc.append({
            "t": int(d["time"]),
            "o": float(d["open"]),
            "h": float(d["high"]),
            "l": float(d["low"]),
            "c": float(d["close"]),
            "v": float(d.get("volumefrom", 0.0)),
        })
    return ohlc


# =========================
# Sentimento (News/Twitter)
# =========================
def get_sentiment(sym: str, last_price: Optional[float]) -> Dict[str, Any]:
    """
    Integra com sentiment_analyzer.get_sentiment_for_symbol se existir.
    Fallback: neutro (50%).
    """
    try:
        from sentiment_analyzer import get_sentiment_for_symbol  # seu arquivo enviado
        sent = get_sentiment_for_symbol(symbol=sym, last_price=last_price)
        # esperado: {"score": float(0..100), "news_n": int, "tw_n": int}
        if isinstance(sent, dict) and "score" in sent:
            return {
                "score": float(sent.get("score", 50.0)),
                "news_n": int(sent.get("news_n", 0)),
                "tw_n": int(sent.get("tw_n", 0)),
            }
    except Exception as e:
        _warn(f"[SENT] erro {sym}: {e}")
    return {"score": 50.0, "news_n": 0, "tw_n": 0}


# =========================
# Score técnico (fallback)
# =========================
def _sma(values: List[float], win: int) -> float:
    if len(values) < win:
        return float("nan")
    return sum(values[-win:]) / win


def _rsi(values: List[float], win: int = 14) -> float:
    if len(values) < win + 1:
        return float("nan")
    gains, losses = 0.0, 0.0
    for i in range(-win, 0):
        delta = values[i] - values[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    rs = gains / max(1e-9, losses)
    return 100.0 - (100.0 / (1.0 + rs))


def technical_score(ohlc: List[Dict[str, Any]]) -> float:
    """
    1) Se existir função do seu projeto, usa (por ex., apply_strategies.score_from_indicators).
    2) Fallback simples: média de 3 sinais (tendência SMA, RSI, preço vs SMA50).
    Retorna 0..100 (%).
    """
    # 1) tentar função externa do projeto
    try:
        from apply_strategies import score_from_indicators as _score_ext  # se existir
        try:
            return float(_score_ext(ohlc))  # sua função pode aceitar OHLC bruto
        except TypeError:
            pass
    except Exception:
        pass

    # 2) fallback simples
    closes = [x["c"] for x in ohlc]
    if len(closes) < 60:
        return 0.0
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    rsi14 = _rsi(closes, 14)
    last = closes[-1]

    score_tend = 100.0 if sma20 > sma50 else 0.0
    score_rsi = max(0.0, min(100.0, (rsi14 - 30.0) * (100.0 / 40.0)))  # 30-70 -> 0..100
    score_price = 100.0 if last > sma50 else 0.0

    return 0.4 * score_tend + 0.3 * score_rsi + 0.3 * score_price


# =========================
# Pipeline
# =========================
def run_pipeline() -> None:
    start = time.time()
    _log("Starting Container")
    _log(f"\u25b6\ufe0f Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")

    _log(f"\ud83d\udd0e NEWS ativo?: {NEWS_USE} | IA ativa?: {AI_ENABLE} | "
         f"Histórico ativado?: {HISTORY_ENABLE} | Twitter ativo?: {TWITTER_USE}")

    # Universo
    universe = get_universe()
    if not universe:
        _err("Sem universo de moedas (CMC/CG indisponíveis ou sem UNIVERSE_LIST/CRYPTOCOMPARE_API_KEY).")
        return

    show = ", ".join(universe[:8])
    _log(f"\ud83d\udd8a\ufe0f Moedas deste ciclo ({min(30,len(universe))}/{len(universe)}): {show}")

    data_raw: Dict[str, Any] = {}
    collected = 0

    for sym in universe[:30]:  # limita por ciclo
        _log(f"\U0001f4c8 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        try:
            ohlc = fetch_ohlc_cc(sym, CANDLES_4H)
        except Exception as e:
            _warn(f"{sym}: OHLC insuficiente/erro ({e})")
            continue

        if len(ohlc) < max(60, CANDLES_4H // 2):
            _warn(f"{sym}: OHLC insuficiente ({len(ohlc)}/{CANDLES_4H})")
            continue

        _ok(f"candles={len(ohlc)}")

        last_close = float(ohlc[-1]["c"])

        # score técnico
        try:
            tech = float(technical_score(ohlc))
        except Exception as e:
            _warn(f"{sym} score técnico erro: {e}")
            tech = 0.0

        # sentimento
        sent = get_sentiment(sym, last_close)
        sent_score = float(sent.get("score", 50.0))
        news_n = int(sent.get("news_n", 0))
        tw_n = int(sent.get("tw_n", 0))

        # mix
        mix = (tech * MIX_TECH_OVER_SENT + sent_score * MIX_SENT_OVER_TECH) / (
            MIX_TECH_OVER_SENT + MIX_SENT_OVER_TECH
        )

        _log(
            f"[IND] {sym} | Técnico: {tech:.1f}% | Sentimento: {sent_score:.1f}% "
            f"(news n={news_n}, tw n={tw_n}) | Mix(T:{MIX_TECH_OVER_SENT:.1f},S:{MIX_SENT_OVER_TECH:.1f}): "
            f"{mix:.1f}% (min {MIX_MIN_THRESHOLD:.0f}%)"
        )

        data_raw[sym] = {
            "last": last_close,
            "ohlc": ohlc,
            "tech": tech,
            "sent": sent_score,
            "news_n": news_n,
            "tw_n": tw_n,
            "mix": mix,
        }
        collected += 1

    # persistência simples
    try:
        with open("data_raw.json", "w", encoding="utf-8") as f:
            json.dump(data_raw, f)
        _log(f"\U0001f4be Salvo data_raw.json ({collected} ativos)")
    except Exception as e:
        _warn(f"Não foi possível salvar data_raw.json: {e}")

    _log(f"\U0001f553 Fim: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    _log(f"\u2705 Ciclo concluído em {int(time.time()-start)}s. Próxima execução")


# =========================
# Entry point
# =========================
if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception:
        traceback.print_exc()
        _err("Erro inesperado no ciclo.")
