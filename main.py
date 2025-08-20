# -*- coding: utf-8 -*-
"""
main.py — pipeline completo (técnico + sentimento news/twitter) com logs estáveis.

• runner.py chama:  main.run_pipeline()
• Salva data_raw.json compatível com backtest
• Tolera envs vazios e módulos ausentes
• Mostra contagem de notícias e tweets (news n=.., tw n=..)
"""

from __future__ import annotations
import os, json, time, math, traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple, Optional

# =========================
# Helpers de ENV seguros
# =========================
def _env_bool(name: str, default: bool=False) -> bool:
    val = os.getenv(name, "")
    if val == "" or val is None:
        return default
    val = str(val).strip().lower()
    return val in ("1","true","t","yes","y","on")

def _env_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name, "")
        return int(v) if str(v).strip() != "" else default
    except Exception:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name, "")
        return float(v) if str(v).strip() != "" else default
    except Exception:
        return default

def _utc() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


# ==================================
# Imports opcionais do seu projeto
# ==================================
# Data (OHLC / universe)
try:
    from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
except Exception:
    fetch_ohlc = None
    fetch_top_symbols = None

# Técnicos / score
try:
    from apply_strategies import score_signal as _score_signal  # função livre (float|dict|tuple)
except Exception:
    _score_signal = None

# Sentimento (agregador) — NÃO exige last_price
try:
    from sentiment_analyzer import get_sentiment_for_symbol as _sent_for_symbol
except Exception:
    _sent_for_symbol = None

# Opcional: salvar cache por símbolo
try:
    import history_manager as _hist
except Exception:
    _hist = None


# =========================
# Utilidades locais
# =========================
_STABLES = ("USDT","BUSD","USDC","FDUSD","TUSD","PYUSD","DAI","EURT","EURC","UST")

def _is_stable_pair(sym: str) -> bool:
    # Heurística simples: contém 2 estáveis no mesmo par (ex.: FDUSDUSDT)
    up = sym.upper()
    hits = [s for s in _STABLES if s in up]
    return len(hits) >= 2

def _safe_score_tech(ohlc_rows: List[List[float]]) -> float:
    """Normaliza o retorno do score técnico para [0..1]."""
    if not ohlc_rows or not _score_signal:
        return 0.0
    try:
        s = _score_signal(ohlc_rows)
        # várias formas possíveis retornadas pela sua base
        if isinstance(s, dict):
            s = s.get("score", s.get("value", 0.0))
        elif isinstance(s, tuple):
            s = s[0] if len(s) > 0 else 0.0
        s = float(s)
        if s > 1.00001:  # se veio em %
            s = s / 100.0
        return max(0.0, min(1.0, s))
    except Exception:
        return 0.0

def _safe_sentiment(symbol: str) -> Dict[str, Any]:
    """Chama o agregador de sentimento e devolve dict sempre (score 0..1)."""
    if not _sent_for_symbol:
        return {"score": 0.5, "news_count": 0, "twitter_count": 0, "source": "disabled"}
    try:
        resp = _sent_for_symbol(symbol)  # NÃO passa last_price
        if isinstance(resp, tuple):
            # aceitar tuple como (score, extras_dict) ou (score,)
            score = float(resp[0]) if resp else 0.5
            extras = resp[1] if len(resp) > 1 and isinstance(resp[1], dict) else {}
            if score > 1.00001: score /= 100.0
            return {
                "score": max(0.0, min(1.0, score)),
                "news_count": int(extras.get("news_count", extras.get("n_news", 0) or 0)),
                "twitter_count": int(extras.get("twitter_count", extras.get("n_tweets", 0) or 0)),
                "source": "tuple"
            }
        elif isinstance(resp, dict):
            score = float(resp.get("score", 0.5) or 0.5)
            if score > 1.00001: score /= 100.0
            return {
                "score": max(0.0, min(1.0, score)),
                "news_count": int(resp.get("news_count", resp.get("n_news", 0) or 0)),
                "twitter_count": int(resp.get("twitter_count", resp.get("n_tweets", 0) or 0)),
                "source": "dict"
            }
        else:
            score = float(resp)
            if score > 1.00001: score /= 100.0
            return {"score": max(0.0, min(1.0, score)), "news_count": 0, "twitter_count": 0, "source": "float"}
    except Exception as e:
        print(f"[SENT] erro {symbol}: {e}")
        return {"score": 0.5, "news_count": 0, "twitter_count": 0, "source": "error"}

def _save_data_raw(symbols: List[str], data_map: Dict[str, Any], path: str="data_raw.json"):
    obj = {"created_at": _utc(), "symbols": symbols, "data": data_map}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(f"💾 Salvo {os.path.basename(path)} ({len(symbols)} ativos)")

def _maybe_save_symbol_cache(symbol: str, bars: List[List[float]]):
    if not _hist:
        return
    try:
        # suporta tanto save_ohlc_symbol quanto save_symbol_ohlc (nomes comuns)
        if hasattr(_hist, "save_ohlc_symbol"):
            _hist.save_ohlc_symbol(symbol, bars)
        elif hasattr(_hist, "save_symbol_ohlc"):
            _hist.save_symbol_ohlc(symbol, bars)
    except Exception:
        pass


# =========================
# Pipeline principal
# =========================
def run_pipeline():
    t0 = time.time()

    # ---------- parâmetros ----------
    interval_min = _env_float("RUN_INTERVAL_MIN", 20.0)
    top_n        = _env_int("TOP_SYMBOLS", 100)
    batch_size   = _env_int("BATCH_SIZE", 8)
    days_ohlc    = _env_int("DAYS_OHLC", 30)
    min_bars     = _env_int("MIN_BARS", 180)  # 30d * 6h = 120; 30d*4h=180 etc
    thr_mix      = _env_float("SCORE_THRESHOLD", 0.70)

    w_tech       = _env_float("WEIGHT_TECH", 1.0)
    w_sent       = _env_float("WEIGHT_SENT", 1.0)

    # flags
    news_enabled    = bool(os.getenv("NEWS_API_KEY")) and _env_bool("NEWS_USE", True)
    twitter_enabled = bool(os.getenv("TWITTER_BEARER_TOKEN")) and _env_bool("TWITTER_USE", True)
    ai_enabled      = _env_bool("AI_FEATURES", True)
    hist_enabled    = _env_bool("SAVE_HISTORY", True)

    print(f"▶️ Runner iniciado. Intervalo = {interval_min:.1f} min.")
    print(f"🔎 NEWS ativo?: {news_enabled} | IA ativa?: {ai_enabled} | Histórico ativado?: {hist_enabled} | Twitter ativo?: {twitter_enabled}")

    # ---------- universo ----------
    symbols_env = [s for s in (os.getenv("SYMBOLS","").replace(" ","").split(",")) if s]
    if symbols_env:
        universe = symbols_env
    elif fetch_top_symbols:
        try:
            universe = fetch_top_symbols(top_n) or []
        except Exception:
            universe = []
    else:
        # fallback mínimo
        universe = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

    # remove estáveis redundantes
    before = len(universe)
    universe = [s for s in universe if not _is_stable_pair(s)]
    removed = before - len(universe)
    if removed > 0:
        print(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")

    # lote do ciclo
    batch = universe[:batch_size]
    print(f"🧪 Moedas deste ciclo ({len(batch)}/{len(universe)}): {', '.join(batch)}")

    # ---------- coleta OHLC ----------
    collected: Dict[str, List[List[float]]] = {}
    for sym in batch:
        print(f"📊 Coletando OHLC {sym} (days={days_ohlc})…")
        raw = []
        try:
            if fetch_ohlc:
                raw = fetch_ohlc(sym, days_ohlc) or []
            else:
                raw = []
        except Exception as e:
            msg = str(e)
            if "429" in msg:
                print("⚠️ 429: aguardando 30.0s (tentativa 1/6)")
                time.sleep(30.0)
                try:
                    raw = fetch_ohlc(sym, days_ohlc) or []
                except Exception:
                    raw = []
            else:
                print(f"⚠️ Erro OHLC {sym}: {msg}")
                raw = []

        # normaliza: aceitar [[t,o,h,l,c], ...]
        rows = []
        for r in raw:
            try:
                if isinstance(r, list) and len(r) >= 5:
                    rows.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])])
                elif isinstance(r, dict):
                    # aceita dicts: {'t':..,'o':..,'h':..,'l':..,'c':..}
                    t = float(r.get("t", r.get("time", 0.0)))
                    o = float(r.get("o", r.get("open", 0.0)))
                    h = float(r.get("h", r.get("high", 0.0)))
                    l = float(r.get("l", r.get("low", 0.0)))
                    c = float(r.get("c", r.get("close", 0.0)))
                    rows.append([t,o,h,l,c])
            except Exception:
                continue

        if len(rows) < min_bars:
            print(f"⚠️ {sym}: OHLC insuficiente ({len(rows)}/{min_bars})")
        else:
            print(f"   → OK | candles= {len(rows)}")

        collected[sym] = rows
        if hist_enabled and rows:
            _maybe_save_symbol_cache(sym, rows)

    # ---------- persistência ----------
    _save_data_raw(list(collected.keys()), collected, "data_raw.json")

    # ---------- pontuação & sentimento ----------
    for sym, rows in collected.items():
        # técnico
        tech = _safe_score_tech(rows)
        # sentimento
        sent = _safe_sentiment(sym)
        s_news = int(sent.get("news_count", 0))
        s_twit = int(sent.get("twitter_count", 0))
        s_score = float(sent.get("score", 0.5) or 0.5)

        # mix
        mix_num = (tech * w_tech) + (s_score * w_sent)
        mix_den = (w_tech + w_sent) if (w_tech + w_sent) > 0 else 1.0
        mix = max(0.0, min(1.0, mix_num / mix_den))

        print(
            f"[IND] {sym} | Técnico: {tech*100:.1f}% | Sentimento: {s_score*100:.1f}% "
            f"(news n={s_news}, tw n={s_twit}) | Mix(T:{w_tech:.1f},S:{w_sent:.1f}): {mix*100:.1f}% "
            f"(min {int(thr_mix*100)}%)"
        )

    print(f"🕒 Fim: {_utc()}")

if __name__ == "__main__":
    run_pipeline()
