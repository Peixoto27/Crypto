# -*- coding: utf-8 -*-
"""
main.py — pipeline principal
- Coleta OHLC (CoinGecko), com retry e mapeamento CG_IDS
- Salva data_raw.json
- Calcula score técnico (apply_strategies.score_signal)
- Calcula sentimento (sentiment_analyzer.get_sentiment_for_symbol) — retorno unificado
- Mistura técnico + sentimento (pesos do .env)
- Imprime logs detalhados
- Expõe run_pipeline() para o runner
"""

from __future__ import annotations
import os
import json
import time
import math
from typing import Dict, Any, List, Tuple, Optional

# ============== Helpers de ENV ==============
def _b(name: str, default: bool) -> bool:
    v = os.getenv(name, "").strip().lower()
    if v == "":
        return default
    return v in ("1", "true", "yes", "y", "on")

def _f(name: str, default: float) -> float:
    v = os.getenv(name, "").strip()
    if v == "":
        return default
    try:
        return float(v)
    except Exception:
        return default

def _i(name: str, default: int) -> int:
    v = os.getenv(name, "").strip()
    if v == "":
        return default
    try:
        return int(v)
    except Exception:
        return default

def _symbols_from_env() -> List[str]:
    raw = os.getenv("SYMBOLS", "").replace(" ", "")
    if raw:
        return [s for s in raw.split(",") if s]
    # fallback: usa top hardcoded se não houver env
    return ["BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT"]

# ============== Módulos externos (tolerante) ==============
_apply_ok = False
try:
    from apply_strategies import score_signal as _score_signal
    _apply_ok = True
except Exception:
    _apply_ok = False

_df_ok = False
try:
    # seu coletor que chama CoinGecko
    from data_fetcher_coingecko import fetch_ohlc as _fetch_ohlc
    _df_ok = True
except Exception:
    _df_ok = False

_cg_client_ok = False
try:
    # opcional: ajuda a descobrir o id do CoinGecko
    from coingecko_client import guess_id as _cg_guess_id
    _cg_client_ok = True
except Exception:
    _cg_client_ok = False

# sentimento unificado (News + Twitter)
_sent_ok = False
try:
    from sentiment_analyzer import get_sentiment_for_symbol as _get_sentiment
    _sent_ok = True
except Exception:
    _sent_ok = False

# ============== Configs (ENV) ==============
INTERVAL_MIN          = _f("INTERVAL_MIN", 20.0)
DAYS_OHLC             = _i("DAYS_OHLC", 30)
MIN_BARS              = _i("MIN_BARS", 180)

# Pesos para mistura final
TECH_WEIGHT           = _f("WEIGHT_TECH", _f("TECH_WEIGHT", 1.0))
SENT_WEIGHT           = _f("WEIGHT_SENT", _f("SENT_WEIGHT", 1.0))
if TECH_WEIGHT == 0 and SENT_WEIGHT == 0:
    TECH_WEIGHT = 1.0
    SENT_WEIGHT = 1.0

SCORE_THRESHOLD       = _f("SCORE_THRESHOLD", 0.70)  # 0..1
DATA_RAW_FILE         = os.getenv("DATA_RAW_FILE", "data_raw.json")

# Toggles (apenas para exibir no cabeçalho)
NEWS_ACTIVE           = _b("NEWS_USE", True) or _b("ENABLE_NEWS", True)
IA_ACTIVE             = _b("IA_USE", True) or _b("ENABLE_AI", True)
HISTORY_ACTIVE        = _b("SAVE_HISTORY", False)
TWITTER_ACTIVE        = _b("TWITTER_USE", False)

# Retry OHLC
RETRY_BASE_WAIT       = _f("RETRY_BASE_WAIT", 30.0)   # 1ª espera
RETRY_MULTIPLIER      = _f("RETRY_MULTIPLIER", 2.5)   # 2ª espera = base*2.5; 3ª = base*2.5^2 etc
RETRY_MAX_ATTEMPTS    = _i("RETRY_MAX_ATTEMPTS", 6)

# Remove pares estáveis (FDUSDUSDT, USDTUSDT, etc.)
REMOVE_STABLES        = _b("REMOVE_STABLES", True)
STABLE_KEYWORDS       = ["FDUSD", "USDC", "BUSD", "TUSD", "USDT", "USDX", "USDP"]  # heurística

# ============== CG Ids ==============
CG_IDS_FILE = os.getenv("CG_IDS_FILE", "cg_ids.json")

def _load_cg_ids() -> Dict[str, str]:
    if not os.path.exists(CG_IDS_FILE):
        return {}
    try:
        with open(CG_IDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_cg_ids(mapping: Dict[str, str]) -> None:
    try:
        with open(CG_IDS_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _is_stable(symbol: str) -> bool:
    base = symbol.replace("USDT","").replace("USDC","")
    if base in ("", "USD"):
        return True
    # remove exemplares óbvios tipo FDUSDUSDT
    for k in STABLE_KEYWORDS:
        if symbol.startswith(k) or symbol.endswith(k):
            # FDUSDUSDT, USDTUSDT, etc.
            if symbol.endswith("USDT") and symbol[:-4] in ("USDT", "FDUSD","USDC","BUSD","TUSD","USDP","USDX"):
                return True
    return False

# ============== Coleta OHLC com retry ==============
def _retry_wait(attempt: int) -> float:
    # attempt começa em 1
    return RETRY_BASE_WAIT * (RETRY_MULTIPLIER ** (attempt - 1))

def _collect_ohlc(symbol: str, cg_id: str, days: int) -> Optional[List[List[float]]]:
    """
    Espera lista no formato [[ts,o,h,l,c], ...]
    """
    if not _df_ok:
        print(f"⚠️ Erro OHLC {symbol}: data_fetcher_coingecko.fetch_ohlc não disponível")
        return None

    for att in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            rows = _fetch_ohlc(symbol, days, cg_id=cg_id)  # seu fetch aceita cg_id (ajuste se não aceitar)
            if isinstance(rows, list) and rows:
                return rows
            raise RuntimeError("sem dados")
        except Exception as e:
            if att >= RETRY_MAX_ATTEMPTS:
                print(f"❌ Erro OHLC {symbol}: {e}")
                return None
            wait = _retry_wait(att)
            print(f"⚠️ 429: aguardando {wait:.1f}s (tentativa {att}/{RETRY_MAX_ATTEMPTS})")
            try:
                time.sleep(wait)
            except Exception:
                pass
    return None

def _len_bars(rows: Optional[List]) -> int:
    if not rows:
        return 0
    return len(rows)

# ============== Score técnico com tolerância ==============
def _safe_tech_score(ohlc_rows: List[List[float]]) -> float:
    """
    Aceita qualquer forma de retorno do score_signal:
    - float (0..1 ou 0..100)
    - dict com chave "score" (0..1/0..100)
    - tuple (score, ...)
    """
    if not _apply_ok:
        return 0.0
    try:
        res = _score_signal(ohlc_rows)
        if isinstance(res, dict):
            s = float(res.get("score", res.get("value", 0.0)))
        elif isinstance(res, tuple):
            s = float(res[0])
        else:
            s = float(res)
        if s > 1.0:
            s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        print(f"[IND] erro em score_signal: {e}")
        return 0.0

# ============== Sentimento normalizado ==============
def _normalize_sent_output(sent: Any) -> Dict[str, Any]:
    """
    Garante dict:
    {"score":float,"parts":{"news":x,"twitter":y},"counts":{"news":n1,"twitter":n2}}
    Aceita dict/tuple/float.
    """
    if isinstance(sent, dict):
        score = float(sent.get("score", 0.5))
        parts  = sent.get("parts", {}) if isinstance(sent.get("parts", {}), dict) else {}
        counts = sent.get("counts", {}) if isinstance(sent.get("counts", {}), dict) else {}
        news_s = float(parts.get("news", 0.5))
        tw_s   = float(parts.get("twitter", 0.5))
        n_news = int(counts.get("news", 0))
        n_tw   = int(counts.get("twitter", 0))
        pass
    elif isinstance(sent, tuple):
        score = float(sent[0]) if len(sent) > 0 else 0.5
        if score > 1.0: score /= 100.0
        news_s, tw_s = score, 0.5
        n_news, n_tw = (int(sent[1]) if len(sent) > 1 else 0), 0
    else:
        try:
            score = float(sent)
        except Exception:
            score = 0.5
        if score > 1.0: score /= 100.0
        news_s, tw_s = score, 0.5
        n_news, n_tw = 0, 0

    score = max(0.0, min(1.0, score))
    return {
        "score": score,
        "parts": {"news": max(0.0, min(1.0, news_s)), "twitter": max(0.0, min(1.0, tw_s))},
        "counts": {"news": n_news, "twitter": n_tw},
    }

# ============== Utilidades diversas ==============
def _mix(tech: float, sent: float) -> float:
    w = TECH_WEIGHT + SENT_WEIGHT
    if w <= 0:
        return 0.0
    return (tech * TECH_WEIGHT + sent * SENT_WEIGHT) / w

def _print_header(symbols: List[str], cg_ids: Dict[str, str]) -> None:
    print(f"▶️ Runner iniciado. Intervalo = {INTERVAL_MIN:.1f} min.")
    print(f"🔎 NEWS ativo?: {str(NEWS_ACTIVE)} | IA ativa?: {str(IA_ACTIVE)} | Histórico ativado?: {str(HISTORY_ACTIVE)} | Twitter ativo?: {str(TWITTER_ACTIVE)}")
    # limpeza de estáveis redundantes
    removed = 0
    if REMOVE_STABLES:
        for s in list(symbols):
            if _is_stable(s):
                symbols.remove(s)
                removed += 1
    if removed > 0:
        print(f"🧠 Removidos {removed} pares estáveis redundantes (ex.: FDUSDUSDT).")
    total = len(symbols)
    print(f"🧪 Moedas deste ciclo ({min(total,8)}/{total}): {', '.join(symbols[:8])}")

def _ensure_dirs():
    d = os.path.dirname(DATA_RAW_FILE)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

# ============== Pipeline ==============
def run_pipeline():
    symbols = _symbols_from_env()
    cg_ids: Dict[str, str] = _load_cg_ids()
    _ensure_dirs()

    _print_header(symbols, cg_ids)

    collected: Dict[str, List[List[float]]] = {}
    ok_symbols: List[str] = []

    # --- Coleta OHLC ---
    for sym in symbols[:8]:  # mantém a janela de 8 por ciclo, como no seu runner
        print(f"📊 Coletando OHLC {sym} (days={DAYS_OHLC})…")
        # resolve cg_id
        cg_id = cg_ids.get(sym)
        if not cg_id and _cg_client_ok:
            try:
                cg_id = _cg_guess_id(sym)  # sua função pode mapear "BTCUSDT"->"bitcoin"
            except Exception:
                cg_id = None
        if cg_id:
            # log “CG_IDS atualizado”
            base = cg_id.replace("-", " ")
            print(f"🟦 CG_IDS atualizado: {sym} -> {cg_id}")
        else:
            print(f"🟨 Sem mapeamento CoinGecko para {sym}. Adicione em CG_IDS.")

        rows = _collect_ohlc(sym, cg_id, DAYS_OHLC)
        if rows and _len_bars(rows) >= MIN_BARS:
            collected[sym] = rows
            ok_symbols.append(sym)
            print(f"   → OK | candles={_len_bars(rows)}")
        else:
            print(f"⚠️ Erro OHLC {sym}: dados insuficientes")

    # salva data_raw.json
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_symbols, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_symbols)} ativos)")
    except Exception as e:
        print(f"❌ Falha ao salvar {DATA_RAW_FILE}: {e}")

    # --- Scoring por ativo ---
    for sym in ok_symbols:
        rows = collected.get(sym, [])
        # técnico
        tech = _safe_tech_score(rows)

        # sentimento
        if _sent_ok:
            raw_sent = _get_sentiment(sym)
            S = _normalize_sent_output(raw_sent)
        else:
            S = {"score": 0.5, "parts": {"news": 0.5, "twitter": 0.5}, "counts": {"news": 0, "twitter": 0}}

        sent = S["score"]
        news_n = S["counts"]["news"]
        tw_n   = S["counts"]["twitter"]

        mix = _mix(tech, sent)

        # printa linha detalhada (estilo que você vinha usando)
        print(f"[IND] {sym} | Técnico: {round(tech*100,1)}% | "
              f"Sentimento: {round(sent*100,1)}% (news n={news_n}, tw n={tw_n}) | "
              f"Mix(T:{TECH_WEIGHT},S:{SENT_WEIGHT}): {round(mix*100,1)}% (min {int(SCORE_THRESHOLD*100)}%)")

    print("🕒 Fim: " + time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()))

# ============== Bootstrap ==============
if __name__ == "__main__":
    run_pipeline()
