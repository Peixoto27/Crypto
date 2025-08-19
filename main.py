# -*- coding: utf-8 -*-
"""
main.py — ciclo de coleta + score técnico + sentimento + mistura
Compatível com history_manager.save_ohlc_symbol()
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any, Tuple

from data_fetcher_coingecko import fetch_ohlc as cg_fetch_ohlc
from apply_strategies import score_signal as tech_score
from sentiment_analyzer import get_sentiment_for_symbol
from history_manager import save_ohlc_symbol  # <= agora existe

# ----------------- helpers -----------------

def _env(name: str, default: str) -> str:
    v = os.getenv(name, default)
    return v if v is not None else default

def _b(name: str, default: bool=False) -> bool:
    return _env(name, str(default).lower()).strip().lower() in ("1","true","yes","sim","on")

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _print(*a): print(*a, flush=True)

def _normalize_ohlc(rows: List) -> List[Dict[str, float]]:
    out = []
    if not rows:
        return out
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                try:
                    out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                                "l": float(r[3]), "c": float(r[4])})
                except Exception:
                    pass
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            try:
                o = float(r.get("open", r.get("o", 0.0)))
                h = float(r.get("high", r.get("h", 0.0)))
                l = float(r.get("low",  r.get("l", 0.0)))
                c = float(r.get("close",r.get("c", 0.0)))
                t = float(r.get("t", r.get("time", 0.0)))
                out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
            except Exception:
                pass
    return out

def _safe_len(x) -> int:
    try: return len(x)
    except Exception: return 0

def _fetch_ohlc_need(symbol: str, start_days: int, need_bars: int, max_days: int) -> List[Dict[str, float]]:
    days = start_days
    last = []
    while days <= max_days:
        _print(f"📊 Coletando OHLC {symbol} (days={days})…")
        try:
            raw = cg_fetch_ohlc(symbol, days)
            bars = _normalize_ohlc(raw)
            n = _safe_len(bars)
            if n >= need_bars:
                _print("   → OK | candles=", n)
                return bars
            else:
                _print(f"⚠️ {symbol}: OHLC insuficiente ({n}/{need_bars})")
                last = bars
        except Exception as e:
            _print(f"⚠️ Erro OHLC {symbol}: {e}")
        days = min(max_days, days + max(5, days // 2)) if days < max_days else max_days + 1
    _print(f"→ OK | candles={_safe_len(last)}")
    return last

def _safe_tech_score(ohlc: List[Dict[str, float]]) -> float:
    try:
        val = tech_score(ohlc)
        if isinstance(val, dict):
            s = float(val.get("score", val.get("value", 0.0)))
        elif isinstance(val, (tuple, list)):
            s = float(val[0]) if val else 0.0
        else:
            s = float(val)
        if s > 1.0: s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        _print(f"[IND] erro em score_signal: {e}")
        return 0.0

def _safe_sentiment(symbol: str, price: float, use_news: bool, use_tw: bool) -> Tuple[float, Dict[str, Any]]:
    try:
        res = get_sentiment_for_symbol(symbol, last_price=price, use_news=use_news, use_twitter=use_tw)
        if isinstance(res, dict):
            s = float(res.get("score", res.get("value", 0.5)))
            if s > 1.0: s /= 100.0
            return (max(0, min(1, s)), res)
        if isinstance(res, (tuple, list)):
            s = 0.5
            info = {}
            if len(res) >= 1:
                try: s = float(res[0])
                except Exception: s = 0.5
            if len(res) >= 2 and isinstance(res[1], dict):
                info = res[1]
            if s > 1.0: s /= 100.0
            return (max(0, min(1, s)), info)
        s = float(res)
        if s > 1.0: s /= 100.0
        return (max(0, min(1, s)), {"raw": res})
    except Exception as e:
        _print(f"[SENT] erro {symbol}: {e}")
        return (0.5, {"error": str(e)})

def _mix(tech: float, sent: float, wt: float, ws: float) -> float:
    try:
        m = (tech*wt + sent*ws) / max(1e-9, (wt+ws))
        return max(0.0, min(1.0, m))
    except Exception:
        return 0.0

def _save_data_raw(path: str, collected: Dict[str, List[Dict[str, float]]]):
    try:
        out = {s: [[r["t"], r["o"], r["h"], r["l"], r["c"]] for r in rows]
               for s, rows in collected.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"created_at": _ts(), "data": out}, f, ensure_ascii=False)
        _print(f"💾 Salvo {os.path.basename(path)} ({len(collected)} ativos)")
    except Exception as e:
        _print(f"⚠️ Erro ao salvar {path}: {e}")

def _select_symbols() -> List[str]:
    s_env = _env("SYMBOLS", "").replace(" ", "")
    if s_env:
        return [s for s in s_env.split(",") if s]
    return ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

# ----------------- pipeline -----------------

def run_pipeline():
    start_days = int(_env("DAYS_OHLC", "30"))
    max_days   = int(_env("MAX_DAYS_OHLC", "60"))
    min_bars   = int(_env("MIN_BARS", "180"))
    batch_size = max(1, int(_env("BATCH_SIZE", "8")))

    use_news   = _b("NEWS_USE", True)
    use_tw     = _b("TWITTER_USE", True)
    ai_on      = _b("AI_USE", True)
    save_hist  = _b("SAVE_HISTORY", True)

    w_t        = float(_env("WEIGHT_TECH", "1.0"))
    w_s        = float(_env("WEIGHT_SENT", "0.5"))
    thr        = float(_env("THRESHOLD_MIX", "0.70"))

    data_raw   = _env("DATA_RAW_FILE", "data_raw.json")
    hist_dir   = _env("HISTORY_DIR", "data/history")

    _print("▶️ Runner iniciado. Intervalo =", f"{_env('INTERVAL_MIN','20.0')} min.")
    _print(f"🔎 NEWS ativo?: {use_news} | IA ativa?: {ai_on} | Histórico ativado?: {save_hist} | Twitter ativo?: {use_tw}")

    symbols_all = _select_symbols()
    symbols = symbols_all[:batch_size]
    _print(f"🧪 Moedas deste ciclo ({len(symbols)}/{len(symbols_all)}): " + ", ".join(symbols) if symbols else "❌ Nenhuma moeda.")

    if not symbols:
        return

    collected: Dict[str, List[Dict[str, float]]] = {}
    for sym in symbols:
        rows = _fetch_ohlc_need(sym, start_days, min_bars, max_days)
        if _safe_len(rows) >= min_bars:
            collected[sym] = rows
            if save_hist:
                try:
                    save_ohlc_symbol(sym, rows, hist_dir)
                except Exception as e:
                    _print(f"⚠️ Falha ao salvar histórico {sym}: {e}")

    _save_data_raw(data_raw, collected)

    if not collected:
        _print("❌ 0 ativos válidos no ciclo — encerrando.")
        _print(f"🕒 Fim: {_ts()}")
        return

    for sym, bars in collected.items():
        close = bars[-1]["c"]
        ts = _safe_tech_score(bars)
        ss, info = _safe_sentiment(sym, close, use_news, use_tw)
        mix = _mix(ts, ss, w_t, w_s)
        news_n = info.get("news_n", info.get("n_news", 0)) if isinstance(info, dict) else 0
        tw_n   = info.get("tw_n",   info.get("n_tweets", 0)) if isinstance(info, dict) else 0
        _print(f"[IND] {sym} | Técnico: {ts*100:.1f}% | Sentimento: {ss*100:.1f}% (news n={news_n}, tw n={tw_n}) | Mix(T:{w_t:.1f},S:{w_s:.1f}): {mix*100:.1f}% (min {int(thr*100)}%)")

    _print(f"🕒 Fim: {_ts()}")

if __name__ == "__main__":
    run_pipeline()
