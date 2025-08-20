# -*- coding: utf-8 -*-
"""
main.py
Runner principal com:
- Universo via CoinMarketCap (CMC) com fallback para estáticos
- OHLC via CryptoCompare (CC)
- Logs compatíveis com seu formato anterior
- Integração com seu sentiment_analyzer (se presente)
"""

import os
import json
import time
from datetime import datetime, timezone

# --------- FLAGS / CONFIG ---------
CYCLE_LIMIT = int(os.getenv("CYCLE_COINS_LIMIT", "100"))      # até 100 moedas
DAYS = int(os.getenv("OHLC_DAYS", "30"))                      # janela
MIN_CANDLES = int(os.getenv("MIN_CANDLES", "60"))             # mínimo p/ aceitar
INTERVAL = os.getenv("OHLC_INTERVAL", "4h")                   # 4h ou 1d

USE_NEWS = os.getenv("NEWS_USE", "true").lower() == "true"
USE_TW   = os.getenv("TWITTER_USE", "true").lower() == "true"
USE_AI   = os.getenv("IA_USE", "true").lower() == "true"
USE_HISTORY = os.getenv("HISTORY_USE", "true").lower() == "true"

MIX_TECH_OVER_SENT = float(os.getenv("MIX_TECH_OVER_SENT", "1.5"))
MIX_SENT_OVER_TECH = float(os.getenv("MIX_SENT_OVER_TECH", "1.0"))
MIX_MIN_THRESHOLD  = float(os.getenv("MIX_MIN_THRESHOLD", "70"))

# --------- IMPORTS LOCAIS ---------
try:
    from data_fetcher_cmc import get_universe
except Exception:
    get_universe = None

try:
    from data_fetcher_cc import fetch_ohlc_cc
except Exception:
    fetch_ohlc_cc = None

# Indicadores/estratégias (mantém teu pipeline se existir)
_compute_indicators = None
try:
    from apply_strategies import score_from_indicators as _compute_indicators
except Exception:
    pass  # se não existir, calculamos score simples

# Sentimento (usa teu módulo se existir)
_get_sentiment = None
try:
    from sentiment_analyzer import get_sentiment_for_symbol as _get_sentiment
except Exception:
    pass

# ---------- UTILS/LOG ----------
def _log(msg: str): print(msg, flush=True)
def _now_utc_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# ---------- UNIVERSO ----------
STATIC_FALLBACK = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
    "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
    "BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT","XLMUSDT",
]

def load_universe(limit:int) -> list:
    coins = []
    # tenta CMC
    if get_universe:
        try:
            coins = get_universe(limit=limit)
        except Exception as e:
            _log(f"⚠️  CMC indisponível: {e}")
    # fallback estático
    if not coins:
        coins = STATIC_FALLBACK[:limit]
    return coins

# ---------- OHLC ----------
def fetch_ohlc(symbol: str, days:int, interval:str):
    if not fetch_ohlc_cc:
        raise RuntimeError("data_fetcher_cc não disponível")
    return fetch_ohlc_cc(symbol, days=days, interval=interval)

# ---------- TÉCNICO (fallback simples se apply_strategies não existir) ----------
def _naive_score_from_prices(ohlc: list) -> float:
    if len(ohlc) < 14:
        return 0.0
    closes = [r[4] for r in ohlc]
    # RSI simples (Wilder-like) p/ ter algo quando faltar teu módulo:
    gains, losses = 0.0, 0.0
    for i in range(1,15):
        delta = closes[-i] - closes[-i-1]
        if delta >= 0: gains += delta
        else: losses -= delta
    if losses == 0:
        rsi = 70.0
    else:
        rs = (gains/14.0)/(losses/14.0)
        rsi = 100 - (100/(1+rs))
    # score heurístico só p/ não zerar (quanto mais sobrevendido, maior score)
    score = max(0.0, min(100.0, (50.0 - abs(rsi-50))*2))
    return score

def compute_tech_score(symbol:str, ohlc:list) -> float:
    if _compute_indicators:
        try:
            return float(_compute_indicators(symbol, ohlc))  # tua função existente
        except Exception as e:
            _log(f"⚠️  IND erro {symbol}: {e}")
    return _naive_score_from_prices(ohlc)

# ---------- SENTIMENTO ----------
def compute_sentiment(symbol:str) -> dict:
    if _get_sentiment:
        try:
            return _get_sentiment(symbol) or {"score":50.0,"news_n":0,"tw_n":0}
        except Exception as e:
            _log(f"[SENT] erro {symbol}: {e}")
    return {"score":50.0,"news_n":0,"tw_n":0}

# ---------- PIPELINE ----------
def run_pipeline():
    _log("Starting Container")
    _log(f"▶️ Runner iniciado. Intervalo = {float(os.getenv('RUN_INTERVAL_MIN','20')):.1f} min.")
    _log(f"🔎 NEWS ativo?: {USE_NEWS} | IA ativa?: {USE_AI} | Histórico ativado?: {USE_HISTORY} | Twitter ativo?: {USE_TW}")

    symbols = load_universe(CYCLE_LIMIT)
    if not symbols:
        _log("❌ Sem universo de moedas (CMC/CG indisponíveis).")
        return

    _log(f"🧪 Moedas deste ciclo ({min(len(symbols), CYCLE_LIMIT)}/{CYCLE_LIMIT}): " + ", ".join(symbols[:30]) + ("..." if len(symbols)>30 else ""))

    collected = {}
    for sym in symbols:
        _log(f"📊 Coletando OHLC {sym} (days={DAYS})…")
        try:
            rows = fetch_ohlc(sym, DAYS, INTERVAL)
            n = len(rows)
            if n < MIN_CANDLES:
                _log(f"⚠️ {sym}: OHLC insuficiente ({n}/{MIN_CANDLES})")
                continue
            _log(f"   → OK | candles= {n}")
            collected[sym] = rows
        except Exception as e:
            _log(f"⚠️ {sym}: OHLC falhou: {e}")

    # salva cache bruto
    try:
        with open("data_raw.json","w",encoding="utf-8") as f:
            json.dump({k: v[-300:] for k,v in collected.items()}, f)
        _log(f"💾 Salvo data_raw.json ({len(collected)} ativos)")
    except Exception as e:
        _log(f"⚠️ Erro ao salvar data_raw.json: {e}")

    # indicadores + sentimento
    for sym, ohlc in collected.items():
        tech = float(compute_tech_score(sym, ohlc) or 0.0)
        sent = compute_sentiment(sym)
        sent_score = float(sent.get("score", 50.0))
        news_n = int(sent.get("news_n", 0))
        tw_n = int(sent.get("tw_n", 0))

        mix = (tech * MIX_TECH_OVER_SENT + sent_score * MIX_SENT_OVER_TECH) / (MIX_TECH_OVER_SENT + MIX_SENT_OVER_TECH)

        _log(f"[IND] {sym} | Técnico: {tech:.1f}% | Sentimento: {sent_score:.1f}% (news n={news_n}, tw n={tw_n}) | "
             f"Mix(T:{MIX_TECH_OVER_SENT:.1f},S:{MIX_SENT_OVER_TECH:.1f}): {mix:.1f}% (min {MIX_MIN_THRESHOLD:.0f}%)")

    _log(f"🕒 Fim: {_now_utc_str()}")
    _log("✅ Ciclo concluído.")

if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as e:
        _log(f"❌ Erro inesperado: {e}")
