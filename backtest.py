# -*- coding: utf-8 -*-
"""
backtest.py — backtest leve (TP/SL) usando histórico local (sem pesar API)

Prioridade de dados OHLC:
  1) HISTORY_DIR/ohlc/{SYMBOL}.json  (cache local por símbolo)
  2) data_raw.json (último ciclo salvo pelo main)
  3) (opcional) CoinGecko via fetch_ohlc() SE ALLOW_API_FALLBACK=true

Env úteis:
  SYMBOLS                  -> lista fixa; se vazio usa TOP_SYMBOLS
  TOP_SYMBOLS=100
  DAYS_OHLC=30
  MIN_BARS=180
  SCORE_THRESHOLD=0.70
  BACKTEST_MAX_TRADES=200
  BACKTEST_MAX_HOLD_BARS=60
  HISTORY_DIR=data/history
  DATA_RAW_FILE=data_raw.json
  ALLOW_API_FALLBACK=false   (default = não chamar API)
"""

import os
import json
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# ---- seu projeto ----
try:
    from data_fetcher_coingecko import fetch_ohlc, fetch_top_symbols
except Exception:
    fetch_ohlc = None
    fetch_top_symbols = lambda n: []
from apply_strategies import score_signal, generate_signal

# --------------------
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _get_env(name: str, default: str) -> str:
    return os.getenv(name, default)

def _norm_list_rows(rows: List) -> List[Dict[str, float]]:
    """
    Normaliza OHLC para lista de dicts: [{t,o,h,l,c}, ...]
    Aceita: [[ts,o,h,l,c], ...] ou [{open,high,low,close}, ...]
    """
    out = []
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

def _load_from_ohlc_cache(symbol: str, history_dir: str) -> List[Dict[str, float]]:
    """
    Tenta ler HISTORY_DIR/ohlc/{SYMBOL}.json com estrutura:
      {"symbol":"BTCUSDT","bars":[ [ts,o,h,l,c], ... ]} ou lista direta
    """
    path = os.path.join(history_dir, "ohlc", f"{symbol}.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "bars" in data:
            return _norm_list_rows(data["bars"])
        if isinstance(data, list):
            return _norm_list_rows(data)
        return []
    except Exception:
        return []

def _load_from_data_raw(symbol: str, data_raw_file: str) -> List[Dict[str, float]]:
    """
    Usa o data_raw.json salvo no último ciclo:
      {"symbols":[...], "data": { "BTCUSDT": [[ts,o,h,l,c],...], ... } }
    """
    if not os.path.exists(data_raw_file):
        return []
    try:
        with open(data_raw_file, "r", encoding="utf-8") as f:
            obj = json.load(f)
        data = obj.get("data", {})
        rows = data.get(symbol)
        return _norm_list_rows(rows)
    except Exception:
        return []

def _load_ohlc(symbol: str, days: int, min_bars: int,
               history_dir: str, data_raw_file: str,
               allow_api: bool) -> List[Dict[str, float]]:
    # 1) cache local por símbolo
    bars = _load_from_ohlc_cache(symbol, history_dir)
    if len(bars) >= min_bars:
        return bars

    # 2) último data_raw.json
    bars = _load_from_data_raw(symbol, data_raw_file)
    if len(bars) >= min_bars:
        return bars

    # 3) fallback API (opcional)
    if allow_api and fetch_ohlc is not None:
        try:
            rows = fetch_ohlc(symbol, days)
            return _norm_list_rows(rows)
        except Exception:
            return []
    return []

def _safe_score(ohlc_slice: List[Dict[str, float]]) -> float:
    try:
        s = score_signal(ohlc_slice)
        if isinstance(s, dict):
            s = float(s.get("score", s.get("value", 0.0)))
        elif isinstance(s, tuple):
            s = float(s[0])
        else:
            s = float(s)
        if s > 1.0: s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception:
        return 0.0

def _simulate_tp_sl(entry: float, tp: float, sl: float,
                    future: List[Dict[str, float]]) -> Tuple[str, int, float]:
    """
    Simula TP/SL: retorna (result, bars, r_mult)
      result: "win" | "loss" | "timeout"
      bars  : quantas velas até saída
      r_mult: R múltiplo aproximado (+R / -1R / 0)
    Regra: primeiro que tocar (high>=TP vence antes de low<=SL).
    Empate na mesma vela resolve por proximidade do open (heurística neutra).
    """
    if not future:
        return ("timeout", 0, 0.0)
    risk = max(1e-9, abs(entry - sl))
    reward = abs(tp - entry)
    r_unit = reward / risk if risk > 0 else 0.0

    for i, bar in enumerate(future, start=1):
        hi = bar["h"]; lo = bar["l"]; op = bar["o"]
        hit_tp = hi >= tp
        hit_sl = lo <= sl
        if hit_tp and not hit_sl:
            return ("win", i, r_unit)
        if hit_sl and not hit_tp:
            return ("loss", i, -1.0)
        if hit_tp and hit_sl:
            d_tp = abs(tp - op); d_sl = abs(sl - op)
            if d_tp < d_sl:
                return ("win", i, r_unit * 0.5)
            else:
                return ("loss", i, -0.5)
    return ("timeout", len(future), 0.0)

def run_backtest():
    symbols_env = [s for s in _get_env("SYMBOLS", "").replace(" ", "").split(",") if s]
    top_n       = int(_get_env("TOP_SYMBOLS", "100"))
    days        = int(_get_env("DAYS_OHLC", "30"))
    min_bars    = int(_get_env("MIN_BARS", "180"))
    thr         = float(_get_env("SCORE_THRESHOLD", "0.70"))
    max_trades  = int(_get_env("BACKTEST_MAX_TRADES", "200"))
    max_hold    = int(_get_env("BACKTEST_MAX_HOLD_BARS", "60"))
    hist_dir    = _get_env("HISTORY_DIR", "data/history")
    data_raw    = _get_env("DATA_RAW_FILE", "data_raw.json")
    allow_api   = _get_env("ALLOW_API_FALLBACK", "false").lower() in ("1","true","yes")

    universe = symbols_env[:] if symbols_env else (fetch_top_symbols(top_n) if fetch_top_symbols else [])

    report: Dict[str, Any] = {
        "created_at": _ts(),
        "params": {
            "days": days, "min_bars": min_bars, "score_threshold": thr,
            "max_trades": max_trades, "max_hold_bars": max_hold,
            "universe_size": len(universe),
            "history_dir": hist_dir, "data_raw_file": data_raw,
            "allow_api_fallback": allow_api
        },
        "symbols": {}
    }

    total_r = 0.0
    total_n = 0

    print(f"▶️ Backtest (off-API) — universo={len(universe)}, dias={days}, min_bars={min_bars}, thr={int(thr*100)}%")
    print(f"   HISTORY_DIR={hist_dir} | data_raw={data_raw} | fallback_api={allow_api}")

    for sym in universe:
        try:
            ohlc = _load_ohlc(sym, days, min_bars, hist_dir, data_raw, allow_api)
            if len(ohlc) < min_bars:
                print(f"⚠️ {sym}: OHLC insuficiente ({len(ohlc)}/{min_bars})")
                continue

            trades = []
            i = min_bars
            step = 1
            while i < len(ohlc) - 2 and len(trades) < max_trades:
                past = ohlc[:i]
                score = _safe_score(past)
                if score >= thr:
                    try:
                        sig = generate_signal(past)
                    except Exception:
                        sig = None
                    if isinstance(sig, dict):
                        entry = float(sig.get("entry") or past[-1]["c"])
                        tp    = float(sig.get("tp")    or entry*1.02)
                        sl    = float(sig.get("sl")    or entry*0.99)
                        future = ohlc[i : min(i+max_hold, len(ohlc))]
                        res, bars, r = _simulate_tp_sl(entry, tp, sl, future)
                        trades.append({
                            "i": i, "time": ohlc[i-1]["t"],
                            "result": res, "bars": bars, "r_mult": round(r,3),
                            "score": round(score,4),
                            "entry": entry, "tp": tp, "sl": sl
                        })
                        i += max(bars, 1)
                        continue
                i += step

            wins = sum(1 for t in trades if t["result"] == "win")
            losses = sum(1 for t in trades if t["result"] == "loss")
            r_sum = sum(t["r_mult"] for t in trades)
            total_r += r_sum
            total_n += len(trades)

            report["symbols"][sym] = {
                "summary": {
                    "n": len(trades),
                    "wins": wins,
                    "losses": losses,
                    "win_rate": round(100.0*wins/len(trades), 2) if trades else 0.0,
                    "r_total": round(r_sum, 3),
                    "r_avg": round(r_sum/len(trades), 3) if trades else 0.0
                },
                "trades": trades
            }
            s = report["symbols"][sym]["summary"]
            print(f"📈 {sym}: n={s['n']} | win%={s['win_rate']} | Rtot={s['r_total']} | Ravg={s['r_avg']}")
        except Exception as e:
            print(f"❌ {sym}: erro no backtest: {e}")

    report["portfolio"] = {
        "trades": total_n,
        "r_total": round(total_r, 3),
        "r_avg": round(total_r/total_n, 3) if total_n else 0.0
    }

    os.makedirs("reports", exist_ok=True)
    out = f"reports/backtest_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"✅ Backtest concluído. n={total_n} | Rtot={report['portfolio']['r_total']} | Ravg={report['portfolio']['r_avg']}")
    print(f"💾 Relatório salvo: {out}")


if __name__ == "__main__":
    run_backtest()
