# -*- coding: utf-8 -*-
"""
main.py — runner do ciclo com coleta OHLC + score técnico + sentimento (news/twitter) + mistura

Novos ENV úteis:
  TIMEFRAME=1h              # 1h, 4h ou 1d
  DAYS_OHLC=30              # ponto de partida; o código aumenta até 60 se faltar candle
  MIN_BARS=180              # mínimo por símbolo, ajustado pelo timeframe automaticamente
  MAX_DAYS_OHLC=60          # limite superior ao tentar completar candles

  SYMBOLS=BTCUSDT,ETHUSDT,...  # se vazio, usa tua lista padrão do projeto
  BATCH_SIZE=8              # quantos por ciclo

  WEIGHT_TECH=1.0
  WEIGHT_SENT=0.5
  THRESHOLD_MIX=0.70        # 70%

  NEWS_USE=true
  TWITTER_USE=true
  AI_USE=true
  SAVE_HISTORY=true
  HISTORY_DIR=data/history
  DATA_RAW_FILE=data_raw.json
"""

import os
import json
import math
import time
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional, Union

# === Módulos do teu projeto (já existentes) ===
from data_fetcher_coingecko import fetch_ohlc as cg_fetch_ohlc   # já tem backoff 429 aí
from apply_strategies import score_signal as tech_score          # teu scoring técnico
from sentiment_analyzer import get_sentiment_for_symbol          # já manda news e twitter
from history_manager import save_ohlc_symbol                     # salva cache por símbolo

# ------------------------------------------------

def _env(name: str, default: str) -> str:
    v = os.getenv(name, default)
    return v if v is not None else default

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _bool_env(name: str, default: bool=False) -> bool:
    v = _env(name, str(default).lower()).strip().lower()
    return v in ("1","true","yes","sim","on")

def _print(*args):
    print(*args, flush=True)

def _timeframe_to_bars_per_day(tf: str) -> int:
    tf = (tf or "1h").lower().strip()
    if tf == "1h":  return 24
    if tf == "4h":  return 6
    if tf == "1d":  return 1
    # default
    return 24

def _needed_bars(min_bars_env: int, timeframe: str) -> int:
    # Mantém o valor alvo, apenas garante >= 60 para dar estabilidade no 1d
    if min_bars_env <= 0:
        min_bars_env = 60
    return int(min_bars_env)

def _safe_len(seq) -> int:
    try:
        return len(seq)
    except Exception:
        return 0

def _normalize_ohlc(rows: List) -> List[Dict[str, float]]:
    """
    Aceita [[ts,o,h,l,c], ...] ou [{t/o/h/l/c}] e padroniza.
    """
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

def _fetch_ohlc_need(symbol: str, timeframe: str, start_days: int, need_bars: int, max_days: int) -> List[Dict[str, float]]:
    """
    Busca OHLC (CoinGecko) começando por 'start_days' e aumentando até 'max_days'
    até atingir 'need_bars'. Evita travar o ciclo — devolve o que conseguir.
    """
    days = start_days
    last = []
    while days <= max_days:
        _print(f"📊 Coletando OHLC {symbol} (days={days})…")
        try:
            raw = cg_fetch_ohlc(symbol, days)   # tua função já lida com 429 com backoff
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
        # incrementa janela para tentar mais candles
        if days < max_days:
            days = min(max_days, int(days * 1.5) if days < 40 else days + 10)
        else:
            break
    # devolve o que tiver (talvez vazio)
    _print(f"→ OK | candles={_safe_len(last)}")
    return last

def _safe_tech_score(ohlc: List[Dict[str, float]]) -> float:
    try:
        val = tech_score(ohlc)  # pode ser float, dict ou tuple (dependendo do teu código)
        if isinstance(val, dict):
            s = float(val.get("score", val.get("value", 0.0)))
        elif isinstance(val, (tuple, list)):
            # primeiro elemento como score
            s = float(val[0]) if val else 0.0
        else:
            s = float(val)
        # normaliza 0..1
        if s > 1.0:
            s = s / 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        _print(f"[IND] erro em score_signal: {e}")
        return 0.0

def _safe_sentiment(symbol: str, price: float, use_news: bool, use_tw: bool) -> Tuple[float, Dict[str, Any]]:
    """
    Retorna (score 0..1, detalhes) aceitando dict/tuple/float da função de sentimento.
    """
    try:
        res = get_sentiment_for_symbol(symbol, last_price=price, use_news=use_news, use_twitter=use_tw)
        if isinstance(res, dict):
            s = float(res.get("score", res.get("value", 0.5)))
            return (max(0.0, min(1.0, s if s <= 1.0 else s/100.0)), res)
        if isinstance(res, (tuple, list)):
            # (score, info)
            s = 0.5
            info = {}
            if len(res) >= 1:
                try:
                    s = float(res[0])
                except Exception:
                    s = 0.5
            if len(res) >= 2 and isinstance(res[1], dict):
                info = res[1]
            if s > 1.0:
                s = s/100.0
            return (max(0.0, min(1.0, s)), info)
        # float puro
        s = float(res)
        if s > 1.0:
            s = s/100.0
        return (max(0.0, min(1.0, s)), {"raw": res})
    except Exception as e:
        _print(f"[SENT] erro {symbol}: {e}")
        return (0.5, {"error": str(e)})

def _mix_score(tech: float, sent: float, w_t: float, w_s: float) -> float:
    # mistura simples ponderada e limita 0..1
    try:
        m = (tech * w_t + sent * w_s) / max(1e-9, (w_t + w_s))
        return max(0.0, min(1.0, m))
    except Exception:
        return 0.0

def _save_data_raw(path: str, collected: Dict[str, List[Dict[str, float]]]):
    try:
        # formato simples: { symbol: [[ts,o,h,l,c], ...], ... }
        out = {}
        for s, rows in collected.items():
            out[s] = [[r["t"], r["o"], r["h"], r["l"], r["c"]] for r in rows]
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"created_at": _ts(), "data": out}, f, ensure_ascii=False)
        _print(f"💾 Salvo {os.path.basename(path)} ({len(collected)} ativos)")
    except Exception as e:
        _print(f"⚠️ Erro ao salvar {path}: {e}")

def _select_symbols() -> List[str]:
    s_env = _env("SYMBOLS", "").replace(" ", "")
    if s_env:
        return [s for s in s_env.split(",") if s]
    # fallback básico — teu repositório normalmente seleciona 90+ via cg_ids
    default8 = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]
    return default8

def run_pipeline():
    # --- ENV/flags ---
    timeframe    = _env("TIMEFRAME", "1h")
    days_start   = int(_env("DAYS_OHLC", "30"))
    max_days     = int(_env("MAX_DAYS_OHLC", "60"))
    min_bars     = _needed_bars(int(_env("MIN_BARS", "180")), timeframe)
    batch_size   = max(1, int(_env("BATCH_SIZE", "8")))

    use_news     = _bool_env("NEWS_USE", True)
    use_twitter  = _bool_env("TWITTER_USE", True)
    ai_on        = _bool_env("AI_USE", True)
    save_hist    = _bool_env("SAVE_HISTORY", True)

    w_t          = float(_env("WEIGHT_TECH", "1.0"))
    w_s          = float(_env("WEIGHT_SENT", "0.5"))
    thr_mix      = float(_env("THRESHOLD_MIX", "0.70"))

    data_raw_file= _env("DATA_RAW_FILE", "data_raw.json")
    hist_dir     = _env("HISTORY_DIR", "data/history")

    # --- status de inicialização ---
    _print("▶️ Runner iniciado. Intervalo =",
           f"{_env('INTERVAL_MIN','20.0')} min.")
    _print(f"🔎 NEWS ativo?: {use_news} | IA ativa?: {ai_on} | Histórico ativado?: {save_hist} | Twitter ativo?: {use_twitter}")

    # --- universo ---
    symbols_all  = _select_symbols()
    # em produção você deve ter a rotação por lote — aqui mantemos simples:
    symbols = symbols_all[:batch_size]
    if not symbols:
        _print("❌ Nenhum símbolo para processar.")
        return

    _print(f"🧪 Moedas deste ciclo ({len(symbols)}/{len(symbols_all)}): " + ", ".join(symbols))

    # --- coleta OHLC ---
    collected: Dict[str, List[Dict[str, float]]] = {}
    for sym in symbols:
        rows = _fetch_ohlc_need(sym, timeframe, days_start, min_bars, max_days)
        if _safe_len(rows) >= min_bars:
            collected[sym] = rows
            # salvar cache por símbolo, se habilitado
            if save_hist:
                try:
                    save_ohlc_symbol(sym, rows, hist_dir)
                except Exception as e:
                    _print(f"⚠️ Falha ao salvar histórico {sym}: {e}")

    # persistir “snapshot” do ciclo
    _save_data_raw(data_raw_file, collected)

    if not collected:
        _print("❌ 0 ativos válidos no ciclo — encerrando.")
        return

    # --- pontuação e sentimento ---
    for sym, bars in collected.items():
        close = bars[-1]["c"] if bars else 0.0

        tscore = _safe_tech_score(bars)
        sscore, sdetail = _safe_sentiment(sym, close, use_news, use_twitter)

        mix = _mix_score(tscore, sscore, w_t, w_s)
        # logs enxutos:
        news_n = sdetail.get("news_n", sdetail.get("n_news", 0)) if isinstance(sdetail, dict) else 0
        tw_n   = sdetail.get("tw_n", sdetail.get("n_tweets", 0)) if isinstance(sdetail, dict) else 0

        _print(f"[IND] {sym} | Técnico: {tscore*100:.1f}% | Sentimento: {sscore*100:.1f}% (news n={news_n}, tw n={tw_n}) | Mix(T:{w_t:.1f},S:{w_s:.1f}): {mix*100:.1f}% (min {int(thr_mix*100)}%)")

    _print(f"🕒 Fim: {_ts()}")

# ponto de entrada esperado pelo runner.py
if __name__ == "__main__":
    run_pipeline()
