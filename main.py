# -*- coding: utf-8 -*-
"""
main.py — pipeline principal robusto
- Coleta OHLC (CoinGecko), calcula Técnico, obtém Sentimentos (News/Twitter),
  mistura e gera sinais.
- Nunca cai por variável de ambiente vazia/ausente.
- Exibe no início: NEWS/IA/Histórico/Twitter ativos?
"""

import os
import json
import time
from math import ceil
from datetime import datetime
from typing import List, Dict, Any, Tuple

# ==== Helpers de ENV robustos ====
def _get_bool_env(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "t", "yes", "y")

def _get_float_env(name: str, default: float = 1.0) -> float:
    try:
        val = os.getenv(name, "")
        if val is None or val.strip() == "":
            return default
        return float(val)
    except Exception:
        return default

def _get_int_env(name: str, default: int = 0) -> int:
    try:
        val = os.getenv(name, "")
        if val is None or val.strip() == "":
            return default
        return int(val)
    except Exception:
        return default

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# ==== Flags / parâmetros ====
ENABLE_NEWS     = _get_bool_env("ENABLE_NEWS", True)
ENABLE_TWITTER  = _get_bool_env("ENABLE_TWITTER", False)
ENABLE_HISTORY  = _get_bool_env("ENABLE_HISTORY", True)
ENABLE_AI       = _get_bool_env("ENABLE_AI", True)

DAYS_OHLC       = _get_int_env("DAYS_OHLC", 30)
MIN_BARS        = _get_int_env("MIN_BARS", 180)
SCAN_BATCH      = _get_int_env("SCAN_BATCH", 8)          # quantos por ciclo
RUN_INTERVAL_MIN= _get_int_env("RUN_INTERVAL_MIN", 20)   # intervalo entre ciclos

WEIGHT_TECH     = _get_float_env("WEIGHT_TECH", 1.5)     # peso para técnico no mix final
WEIGHT_SENT     = _get_float_env("WEIGHT_SENT", 1.0)     # peso p/ sentimento já misturado (news+twitter)

NEWS_API_KEY            = os.getenv("NEWS_API_KEY", "").strip()
TWITTER_BEARER_TOKEN    = os.getenv("TWITTER_BEARER_TOKEN", "").strip()

# ==== Imports do projeto ====
# Coleta de OHLC
_fetch_ohlc = None
try:
    from data_fetcher_coingecko import fetch_ohlc as _fetch_ohlc
except Exception:
    _fetch_ohlc = None

# Cálculo técnico
_score_from_ind = None
try:
    from apply_strategies import score_signal as _score_from_ind
except Exception:
    _score_from_ind = None

# Sentimento (News + Twitter)
try:
    from sentiment_analyzer import get_sentiment_for_symbol
except Exception:
    # fallback neutro
    def get_sentiment_for_symbol(symbol: str) -> Dict[str, Any]:
        return {
            "score": 0.5,
            "news": {"score": 0.5, "n": 0, "enabled": False},
            "twitter": {"score": 0.5, "n": 0, "enabled": False},
        }

# Opcional: histórico
try:
    from history_manager import HistoryManager
except Exception:
    HistoryManager = None


# ===== Utilidades OHLC =====
def _norm_ohlc(rows: List) -> List[Dict[str, float]]:
    """Aceita [[ts,o,h,l,c],…] ou [{t,o,h,l,c},…] → lista de dicts uniformes."""
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
            t = float(r.get("t", r.get("time", 0.0)))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out

def _collect_ohlc(symbol: str, days: int) -> List[Dict[str, float]]:
    """Busca OHLC via CoinGecko. Em falha retorna []."""
    if _fetch_ohlc is None:
        print(f"⚠️ Erro OHLC {symbol}: data_fetcher_coingecko.fetch_ohlc não disponível")
        return []
    try:
        rows = _fetch_ohlc(symbol, days)
        return _norm_ohlc(rows)
    except Exception as e:
        print(f"⚠️ Erro OHLC {symbol}: {e}")
        return []


# ===== Cálculo técnico com tolerância =====
def _safe_tech_score(ohlc: List[Dict[str, float]]) -> float:
    """Retorna score técnico 0..1. Se não houver dados suficientes → 0.0."""
    try:
        if _score_from_ind is None or len(ohlc) < MIN_BARS:
            return 0.0
        s = _score_from_ind(ohlc)
        # normaliza
        if isinstance(s, dict):
            s = float(s.get("score", s.get("value", 0.0)))
        elif isinstance(s, tuple):
            s = float(s[0])
        else:
            s = float(s)
        if s > 1.0:
            s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception:
        return 0.0


# ===== Pipeline =====
def run_pipeline():
    print("Starting Container")
    print(f"▶️ Runner iniciado. Intervalo = {float(RUN_INTERVAL_MIN):.1f} min.")
    print(f"🔎 NEWS ativo?: {str(ENABLE_NEWS and bool(NEWS_API_KEY))} | IA ativa?: {str(ENABLE_AI)} | Histórico ativado?: {str(ENABLE_HISTORY)} | Twitter ativo?: {str(ENABLE_TWITTER and bool(TWITTER_BEARER_TOKEN))}")

    # Universo de símbolos:
    # 1) SYMBOLS (fixo) ou 2) arquivo cg_ids.json (chaves) como fallback
    symbols_env = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
    if symbols_env:
        universe = symbols_env
    else:
        # tenta ler cg_ids.json -> usa as chaves (ex.: BTCUSDT, ETHUSDT...)
        universe = []
        try:
            with open("cg_ids.json", "r", encoding="utf-8") as f:
                cg = json.load(f)
            universe = list(cg.keys())
        except Exception:
            # fallback curtíssimo
            universe = ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

    # remove pares estáveis redundantes (ex.: FDUSDUSDT, USDTUSDT etc.)
    stable_prefixes = ("FDUSD", "USDC", "USDT", "BUSD", "TUSD")
    filtered = [s for s in universe if not (s.startswith(stable_prefixes) and s.endswith("USDT")) or s in ("BTCUSDT","ETHUSDT")]
    removed = len(universe) - len(filtered)
    if removed > 0:
        print(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")
    universe = filtered

    # fatia do ciclo
    batch = universe[:SCAN_BATCH]
    print(f"🧪 Moedas deste ciclo ({len(batch)}/{len(universe)}): {', '.join(batch)}")

    collected: Dict[str, List[Dict[str, float]]] = {}
    for sym in batch:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        raw = _collect_ohlc(sym, DAYS_OHLC)
        if len(raw) >= MIN_BARS:
            collected[sym] = raw
            print(f"   → OK | candles={len(raw)}")
        else:
            print(f"❌ Dados insuficientes para {sym} | candles={len(raw)}")

    # salva data_raw.json para backtest e debug
    to_save = {
        "created_at": _ts(),
        "symbols": list(collected.keys()),
        "data": {k: [[b["t"], b["o"], b["h"], b["l"], b["c"]] for b in v] for k, v in collected.items()}
    }
    with open("data_raw.json", "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)
    print(f"💾 Salvo data_raw.json ({len(collected)} ativos)")

    # opcional: histórico
    hist_mgr = None
    if ENABLE_HISTORY and HistoryManager is not None:
        try:
            hist_dir = os.getenv("HISTORY_DIR", "data/history")
            hist_mgr = HistoryManager(hist_dir)
            for s, bars in collected.items():
                hist_mgr.save_ohlc(s, bars)  # salva por-símbolo
        except Exception:
            hist_mgr = None

    # cálculo de scores
    signals = []
    for sym, bars in collected.items():
        tech = _safe_tech_score(bars)
        sent = get_sentiment_for_symbol(sym)  # já mistura news+twitter internamente

        sent_score = float(sent.get("score", 0.5))
        news_n = int(sent.get("news", {}).get("n", 0))
        tw_n   = int(sent.get("twitter", {}).get("n", 0))

        # mistura final: Técnico x Sentimento
        denom = max(1e-9, (WEIGHT_TECH + WEIGHT_SENT))
        final_score = (tech * WEIGHT_TECH + sent_score * WEIGHT_SENT) / denom

        # logs detalhados (estilo que você usa)
        print(f"[IND] {sym} | Técnico: {tech*100:.1f}% | Sentimento: {sent_score*100:.1f}% (news n={news_n}, tw n={tw_n}) | Mix(T:{WEIGHT_TECH},S:{WEIGHT_SENT}): {final_score*100:.1f}% (min 70%)")

        signals.append({
            "symbol": sym,
            "tech": round(tech, 4),
            "sentiment": {
                "score": round(sent_score, 4),
                "news_n": news_n,
                "tw_n": tw_n
            },
            "mix_score": round(final_score, 4),
            "created_at": _ts()
        })

    # salva sinais e (se quiser) notificações externas
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump({"signals": signals, "created_at": _ts()}, f, ensure_ascii=False, indent=2)

    print(f"🗂 {len([s for s in signals if s['mix_score'] >= 0.70])} sinais salvos em signals.json")
    print(f"🕒 Fim: {_ts()}")


# compatível com o runner.py que chama main.run_pipeline()
if __name__ == "__main__":
    run_pipeline()
