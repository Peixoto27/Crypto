# -*- coding: utf-8 -*-
"""
main.py — pipeline completo com fallback Binance→CoinGecko,
logs amigáveis, salvamento de data_raw.json e integração opcional
com sentimento (news/twitter) + histórico.

Exposto: run_pipeline()   # usado por runner.py
"""

import os
import json
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

# =========================
# Utilidades
# =========================

def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name, "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _print_header():
    print("Starting Container")
    iv = float(os.getenv("INTERVAL_MINUTES", "20"))
    print(f"▶️ Runner iniciado. Intervalo = {iv:.1f} min.")

# =========================
# Imports opcionais do projeto
# =========================

# Fallback de coleta
try:
    from data_fetcher_binance import fetch_ohlc as _fetch_binance
except Exception:
    _fetch_binance = None

try:
    from data_fetcher_coingecko import fetch_ohlc as _fetch_cg
except Exception:
    _fetch_cg = None

# Score técnico
try:
    from apply_strategies import score_signal as _score_signal
except Exception:
    _score_signal = None

# Sentimento (News + Twitter)
try:
    from sentiment_analyzer import get_sentiment_for_symbol as _get_sentiment
except Exception:
    _get_sentiment = None

# Histórico (opcional)
try:
    from history_manager import save_ohlc_symbol as _save_hist
except Exception:
    _save_hist = None

# =========================
# Configs
# =========================

BINANCE_FIRST = _env_bool("BINANCE_FIRST", True)
DAYS_OHLC     = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS      = int(os.getenv("MIN_BARS", "60"))
DATA_RAW_FILE = os.getenv("DATA_RAW_FILE", "data_raw.json")
HISTORY_DIR   = os.getenv("HISTORY_DIR", "data/history")

NEWS_USE      = _env_bool("NEWS_USE", True) or _env_bool("NEWS_ACTIVE", False)
TWITTER_USE   = _env_bool("TWITTER_USE", False)
AI_ACTIVE     = _env_bool("AI_ACTIVE", True)
SAVE_HISTORY  = _env_bool("SAVE_HISTORY", True)

TECH_WEIGHT   = float(os.getenv("WEIGHT_TECH", "1.5"))
SENT_WEIGHT   = float(os.getenv("WEIGHT_SENT", "1.0"))
MIX_MIN       = float(os.getenv("MIX_MIN", "0.70"))  # min 70%

# =========================
# Universo de símbolos
# =========================

_STABLES = {"FDUSDUSDT", "BUSDUSDT", "USDCUSDT", "TUSDUSDT", "USTCUSDT", "DAIUSDT"}

_DEFAULT_UNIVERSE = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
    "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
    "BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT","XLMUSDT",
    "HBARUSDT","INJUSDT","ARBUSDT","LDOUSDT","ATOMUSDT","STXUSDT"
]

def _load_universe() -> List[str]:
    env_syms = os.getenv("SYMBOLS", "").strip()
    if env_syms:
        syms = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        return syms
    # TOP_SYMBOLS ignorado aqui (sem data source interno) → usa default
    return _DEFAULT_UNIVERSE

# =========================
# Coleta com fallback
# =========================

def fetch_ohlc_any(symbol: str, days: int) -> List[List[float]]:
    """
    Retorna OHLC no formato [[ts_ms, o, h, l, c], ...]
    Tenta Binance e cai para CoinGecko (ou só CG se binance indisponível/flag).
    """
    order: List[str]
    if BINANCE_FIRST and _fetch_binance:
        order = ["binance", "coingecko"]
    else:
        order = ["coingecko"]
        if _fetch_binance:
            order.append("binance")

    last_err: Optional[Exception] = None
    for src in order:
        try:
            if src == "binance":
                rows = _fetch_binance(symbol, days)
            else:
                rows = _fetch_cg(symbol, days) if _fetch_cg else []
            # validação básica
            if isinstance(rows, list) and rows and isinstance(rows[0], (list, tuple)) and len(rows[0]) >= 5:
                return [[float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])] for r in rows]
        except Exception as e:
            last_err = e
            print(f"⚠️ {src.capitalize()} falhou {symbol}: {e}")
    if last_err:
        pass  # já logamos acima
    return []

# =========================
# Cálculo seguro de score técnico e sentimento
# =========================

def _safe_score_tech(ohlc_rows: List[List[float]]) -> float:
    """
    Converte saída de _score_signal em [0..1]. Aceita float/tuple/dict/%.
    """
    if not _score_signal:
        return 0.0
    try:
        val = _score_signal(ohlc_rows)
        if isinstance(val, dict):
            val = val.get("score", val.get("value", 0.0))
        if isinstance(val, (list, tuple)) and val:
            val = val[0]
        val = float(val)
        # Se veio em %, normaliza
        if val > 1.0:
            val = val / 100.0
        return max(0.0, min(1.0, val))
    except Exception as e:
        print(f"⚠️ erro em score_signal: {e}")
        return 0.0

def _safe_sentiment(symbol: str) -> Dict[str, Any]:
    """
    Chama sentimento se habilitado. Nunca explode o pipeline.
    Retorna: {"score": 0.0..1.0, "news_n": int, "tw_n": int}
    """
    if not _get_sentiment or (not NEWS_USE and not TWITTER_USE):
        return {"score": 0.5, "news_n": 0, "tw_n": 0}
    try:
        res = _get_sentiment(symbol)  # assinatura sem kwargs (evita erro "last_price")
        # Normalizações de retorno
        score = 0.5
        news_n = 0
        tw_n   = 0
        if isinstance(res, dict):
            if "score" in res:
                score = float(res["score"])
            elif "value" in res:
                score = float(res["value"])
            if "news_n" in res: news_n = int(res["news_n"])
            if "tw_n"   in res: tw_n   = int(res["tw_n"])
        elif isinstance(res, (list, tuple)):
            # tenta (score, news_n, tw_n)
            if len(res) >= 1:
                score = float(res[0])
            if len(res) >= 2:
                news_n = int(res[1] or 0)
            if len(res) >= 3:
                tw_n = int(res[2] or 0)
        else:
            score = float(res)
        if score > 1.0:
            score = score / 100.0
        score = max(0.0, min(1.0, score))
        return {"score": score, "news_n": news_n, "tw_n": tw_n}
    except Exception as e:
        print(f"⚠️ [SENT] erro {symbol}: {e}")
        return {"score": 0.5, "news_n": 0, "tw_n": 0}

def _mix_score(tech: float, sent: float) -> float:
    """
    Combina técnico e sentimento com pesos.
    """
    try:
        mix = (tech * TECH_WEIGHT + sent * SENT_WEIGHT) / max(1e-9, (TECH_WEIGHT + SENT_WEIGHT))
        return max(0.0, min(1.0, mix))
    except Exception:
        return 0.0

# =========================
# Salvamento data_raw.json + histórico
# =========================

def _save_data_raw(collected: Dict[str, List[List[float]]]) -> None:
    obj = {
        "created_at": _ts(),
        "symbols": list(collected.keys()),
        "data": collected
    }
    with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)

def _save_history(symbol: str, rows: List[List[float]]) -> None:
    if not SAVE_HISTORY or not _save_hist:
        return
    try:
        _save_hist(symbol, rows, HISTORY_DIR)
    except Exception as e:
        print(f"⚠️ histórico {symbol}: {e}")

# =========================
# Runner principal
# =========================

def run_pipeline():
    _print_header()
    print(f"🔎 NEWS ativo?: {NEWS_USE} | IA ativa?: {str(AI_ACTIVE).lower()} | Histórico ativado?: {SAVE_HISTORY} | Twitter ativo?: {TWITTER_USE}")

    # Universo e limpeza de estáveis redundantes
    syms = _load_universe()
    removed = [s for s in syms if s in _STABLES]
    if removed:
        print(f"🧠 Removidos {len(removed)} pares estáveis redundantes (ex.: FDUSDUSDT).")
        syms = [s for s in syms if s not in _STABLES]

    max_universe = int(os.getenv("MAX_UNIVERSE", "100"))
    syms = syms[:max_universe]
    print(f"🧪 Moedas deste ciclo ({len(syms)}/{max_universe}): {', '.join(syms)}")

    start = time.time()
    collected: Dict[str, List[List[float]]] = {}

    # 1) Coleta
    for sym in syms:
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        rows = fetch_ohlc_any(sym, DAYS_OHLC)
        if len(rows) < MIN_BARS:
            print(f"⚠️ {sym}: OHLC insuficiente ({len(rows)}/{MIN_BARS})")
            continue
        print(f"   → OK | candles= {len(rows)}")
        collected[sym] = rows
        _save_history(sym, rows)

    # 2) Salva data_raw.json
    try:
        _save_data_raw(collected)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(collected)} ativos)")
    except Exception as e:
        print(f"⚠️ Erro salvando {DATA_RAW_FILE}: {e}")

    # 3) Indicadores / Sentimento / Mix
    for sym, rows in collected.items():
        tech = _safe_score_tech(rows)
        sent_obj = _safe_sentiment(sym)
        sent = float(sent_obj.get("score", 0.5))
        news_n = int(sent_obj.get("news_n", 0))
        tw_n   = int(sent_obj.get("tw_n", 0))
        mix = _mix_score(tech, sent)
        print(f"[IND] {sym} | Técnico: {tech*100:.1f}% | Sentimento: {sent*100:.1f}% (news n={news_n}, tw n={tw_n}) | Mix(T:{TECH_WEIGHT:.1f},S:{SENT_WEIGHT:.1f}): {mix*100:.1f}% (min {int(MIX_MIN*100)}%)")

    # 4) Fechamento
    print(f"🕒 Fim: {_ts()}")
    took = time.time() - start
    print(f"✅ Ciclo concluído em {int(took)}s. Próxima execução")

# Execução direta local
if __name__ == "__main__":
    run_pipeline()
