# -*- coding: utf-8 -*-
"""
main.py — pipeline estável:
- Coleta OHLC (CoinGecko)
- Score técnico (robusto)
- Sentimento NEWS/Twitter (robusto)
- Mistura e geração opcional de sinais
- Logs claros + data_raw.json + signals.json
"""

import os, json, time
from math import ceil
from datetime import datetime
from typing import List, Dict, Tuple

# ===== util =====
def _is_true(name, default=False) -> bool:
    v = str(os.getenv(name, str(default))).strip().lower()
    return v in ("1", "true", "yes", "on")

def _has_text(name) -> bool:
    v = os.getenv(name, "")
    return bool(v and v.strip())

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# ===== fetchers de preço/ohlc com fallback =====
def _norm_rows(rows) -> List[Dict]:
    out = []
    if not rows: return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])})
    elif isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for r in rows:
            o = float(r.get("open", r.get("o", 0.0)))
            h = float(r.get("high", r.get("h", 0.0)))
            l = float(r.get("low",  r.get("l", 0.0)))
            c = float(r.get("close",r.get("c", 0.0)))
            t = float(r.get("t", r.get("time", 0.0)))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out

_fetch_ohlc = None
try:
    from data_fetcher_coingecko import fetch_ohlc as _cg_fetch_ohlc
    _fetch_ohlc = _cg_fetch_ohlc
except Exception:
    try:
        from data_collector import fetch_ohlc as _dc_fetch_ohlc
        _fetch_ohlc = _dc_fetch_ohlc
    except Exception:
        _fetch_ohlc = None

# ===== técnico & sentimento =====
from apply_strategies import score_signal, generate_signal
from sentiment_analyzer import get_sentiment_for_symbol

# ===== universo =====
DEFAULT_SYMBOLS = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
    "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT"
]

STABLE_SUFFIXES = ("USDT", "FDUSD", "BUSD", "USDC")
STABLE_EXACTS   = {"FDUSDUSDT","BUSDUSDT","USDCUSDT"}

def _filter_universe(symbols: List[str]) -> List[str]:
    syms = []
    for s in symbols:
        if s in STABLE_EXACTS:  # redundantes (stable x stable)
            continue
        syms.append(s)
    return syms

def _get_universe() -> List[str]:
    raw = os.getenv("SYMBOLS", "")
    if raw.strip():
        syms = [s.strip().upper() for s in raw.split(",") if s.strip()]
    else:
        syms = DEFAULT_SYMBOLS[:]
    return _filter_universe(syms)

# ===== persistência =====
def _save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

# ===== pipeline =====
def run_pipeline():
    INTERVAL_MIN = float(os.getenv("INTERVAL_MIN", "20"))
    DAYS_OHLC    = int(os.getenv("DAYS_OHLC", "30"))
    MIN_BARS     = int(os.getenv("MIN_BARS", "180"))
    BATCH_SIZE   = int(os.getenv("BATCH_SIZE", "8"))
    THRESHOLD    = float(os.getenv("SCORE_THRESHOLD", "0.70"))
    W_T          = float(os.getenv("WEIGHT_TECH", "1.0"))
    W_S          = float(os.getenv("WEIGHT_SENT", "1.0"))
    DATA_RAW     = os.getenv("DATA_RAW_FILE", "data_raw.json")

    NEWS_ACTIVE = _is_true("NEWS_USE", False) and _has_text("NEWS_API_KEY")
    TW_ACTIVE   = _is_true("TWITTER_USE", False) and _has_text("TWITTER_BEARER_TOKEN")
    AI_ACTIVE   = _is_true("AI_USE", True)
    HIST_ACTIVE = _is_true("SAVE_HISTORY", True)

    news_reason = []
    if not _is_true("NEWS_USE", False):       news_reason.append("NEWS_USE=false")
    if not _has_text("NEWS_API_KEY"):         news_reason.append("NEWS_API_KEY vazio")

    tw_reason = []
    if not _is_true("TWITTER_USE", False):    tw_reason.append("TWITTER_USE=false")
    if not _has_text("TWITTER_BEARER_TOKEN"): tw_reason.append("TWITTER_BEARER_TOKEN vazio")

    print("Starting Container")
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")
    print(
        f"🔎 NEWS ativo?: {NEWS_ACTIVE} "
        f"{'(motivo: ' + ', '.join(news_reason) + ')' if not NEWS_ACTIVE and news_reason else ''} | "
        f"IA ativa?: {AI_ACTIVE} | Histórico ativado?: {HIST_ACTIVE} | "
        f"Twitter ativo?: {TW_ACTIVE} "
        f"{'(motivo: ' + ', '.join(tw_reason) + ')' if not TW_ACTIVE and tw_reason else ''}"
    )

    universe = _get_universe()
    removed = [s for s in universe if s in STABLE_EXACTS]
    if removed:
        print(f"🧠 Removidos {len(removed)} pares estáveis redundantes (ex.: FDUSDUSDT).")
    total = len(universe)
    print(f"🧪 Moedas deste ciclo ({min(BATCH_SIZE,total)}/{total}): {', '.join(universe[:BATCH_SIZE])}")

    collected: Dict[str, List[List[float]]] = {}
    ok_symbols: List[str] = []

    start_t = time.time()

    for sym in universe[:BATCH_SIZE]:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        if _fetch_ohlc is None:
            print(f"⚠️ Erro OHLC {sym}: data_fetcher_coingecko.fetch_ohlc não disponível")
            continue
        backoff = [30.0, 75.0, 187.5]
        tries = 0
        rows = None
        while tries <= len(backoff):
            try:
                rows = _fetch_ohlc(sym, DAYS_OHLC)
                break
            except Exception as e:
                if tries < len(backoff):
                    wait = backoff[tries]
                    print(f"⚠️ 429: aguardando {wait:.1f}s (tentativa {tries+1}/{len(backoff)+1})")
                    time.sleep(wait)
                tries += 1
        bars = _norm_rows(rows)
        print(f"   → OK | candles={len(bars)}")
        if len(bars) >= MIN_BARS:
            collected[sym] = [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in bars]
            ok_symbols.append(sym)

    _save_json(DATA_RAW, {"created_at": _ts(), "symbols": ok_symbols, "data": collected})
    print(f"💾 Salvo {DATA_RAW} ({len(ok_symbols)} ativos)")

    # ==== Scoring & sinais ====
    signals = []
    for sym in ok_symbols:
        bars = [{"t": r[0], "o": r[1], "h": r[2], "l": r[3], "c": r[4]} for r in collected[sym]]
        recent = bars[-MIN_BARS:]

        # técnico
        try:
            tech = score_signal(recent)  # [0..1]
        except Exception:
            tech = 0.0

        # sentimento
        sent, n_news, n_tw = 0.5, 0, 0
        if NEWS_ACTIVE or TW_ACTIVE:
            try:
                sent, n_news, n_tw = get_sentiment_for_symbol(sym)
            except Exception:
                sent, n_news, n_tw = 0.5, 0, 0

        mix = (tech*W_T + sent*W_S) / (W_T + W_S if (W_T + W_S) > 0 else 1.0)

        # log por ativo
        print(f"[IND] close={round(recent[-1]['c'], 2)} | score={round(tech*100,1)}%")
        print(f"[IND] {sym} | Técnico: {round(tech*100,1)}% | Sentimento: {round(sent*100,1)}% (news n={n_news}, tw n={n_tw}) | "
              f"Mix(T:{W_T},S:{W_S}): {round(mix*100,1)}% (min {int(THRESHOLD*100)}%)")

        if mix >= THRESHOLD:
            sig = generate_signal(recent)
            if sig:
                sig["symbol"] = sym
                sig["confidence"] = round(mix*100, 2)
                sig["created_at"] = _ts()
                signals.append(sig)

    _save_json("signals.json", signals)
    print(f"🗂 {len(signals)} sinais salvos em signals.json")
    print(f"🕒 Fim: {_ts()}")
    dur = int(time.time() - start_t)
    nexts = int(INTERVAL_MIN*60)
    print(f"✅ Ciclo concluído em {dur}s. Próxima execução")

if __name__ == "__main__":
    run_pipeline()
