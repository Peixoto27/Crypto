# -*- coding: utf-8 -*-
"""
main.py — pipeline robusto com normalização de OHLC e logs de diagnóstico

- Universo: SYMBOLS do env; se vazio tenta fetch_top_symbols; senão fallback fixo
- Rotaciona lotes por ciclo (scan_state.json)
- Coleta OHLC (CoinGecko e opcional CryptoCompare)
- NORMALIZA OHLC -> [{t,o,h,l,c}]
- Checa dados (remove zeros/NaN; garante MIN_BARS)
- Calcula score técnico (com logs de erro) + mistura com IA (se houver)
- Gera sinal, deduplica e notifica
- Salva cache OHLC por símbolo e data_raw.json
"""

import os
import json
import math
import time
from datetime import datetime
from typing import Dict, Any, List, Optional

# -----------------------------
# Fetchers
# -----------------------------
from data_fetcher_coingecko import fetch_ohlc as cg_fetch_ohlc
try:
    from data_fetcher_coingecko import fetch_top_symbols as cg_fetch_top_symbols  # opcional
except Exception:
    cg_fetch_top_symbols = None

try:
    from data_fetcher_cryptocompare import fetch_ohlc_cc as cc_fetch_ohlc  # opcional
except Exception:
    cc_fetch_ohlc = None

# -----------------------------
# Estratégia / Notificação / Persistência
# -----------------------------
from apply_strategies import score_signal, generate_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal
from history_manager import save_ohlc_cache  # assinatura: save_ohlc_cache(dir, symbol, rows)

# -----------------------------
# IA (opcional)
# -----------------------------
try:
    from model_manager import predict_proba, has_model
except Exception:
    def predict_proba(_: Dict[str, float]) -> Optional[float]:
        return None
    def has_model() -> bool:
        return False

# ==============================
# Config
# ==============================
def _as_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1","true","yes","y","on")

RUN_INTERVAL_MIN   = os.getenv("RUN_INTERVAL_MIN", "20")

SYMBOLS            = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
TOP_SYMBOLS        = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE   = int(os.getenv("SELECT_PER_CYCLE", "8"))

DAYS_OHLC          = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS           = int(os.getenv("MIN_BARS", "180"))

SCORE_THRESHOLD    = float(os.getenv("SCORE_THRESHOLD", "0.45"))  # você está usando ~0.45
MIN_CONFIDENCE     = float(os.getenv("MIN_CONFIDENCE", "0.45"))

WEIGHT_TECH        = float(os.getenv("WEIGHT_TECH", "1.5"))
WEIGHT_AI          = float(os.getenv("WEIGHT_AI", "1.0"))

COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

DATA_RAW_FILE      = os.getenv("DATA_RAW_FILE", "data_raw.json")
HISTORY_DIR        = os.getenv("HISTORY_DIR", "data/history")
CURSOR_FILE        = os.getenv("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE       = os.getenv("SIGNALS_FILE", "data/signals.json")

USE_AI             = _as_bool("USE_AI", "true")
TRAINING_ENABLED   = _as_bool("TRAINING_ENABLED", "true")
USE_NEWS           = _as_bool("USE_RSS_NEW", "false") or _as_bool("USE_THENEWSAPI", "false")
USE_TWITTER        = _as_bool("USE_TWITTER", "false")

REMOVE_STABLES     = _as_bool("REMOVE_STABLES", "true")
STABLE_SUFFIXES    = ("USDT","FDUSD","USDC","BUSD","TUSD")

# ==============================
# Utilidades
# ==============================
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _ensure_cursor() -> Dict[str, Any]:
    try:
        with open(CURSOR_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"offset": 0, "cycle": 0}

def _save_cursor(st: Dict[str, Any]) -> None:
    with open(CURSOR_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

def _rotate(symbols: List[str], take: int) -> List[str]:
    if take <= 0 or not symbols:
        return symbols
    st = _ensure_cursor()
    off = int(st.get("offset", 0)) % len(symbols)
    batch = [symbols[(off + i) % len(symbols)] for i in range(min(take, len(symbols)))]
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _is_stable_pair(symbol: str) -> bool:
    if not REMOVE_STABLES:
        return False
    s = symbol.upper()
    for suf in STABLE_SUFFIXES:
        if s.endswith(suf):
            base = s[:-len(suf)]
            for s2 in STABLE_SUFFIXES:
                if base.endswith(s2):
                    return True
    return False

# -----------------------------
# Universo (com fallback)
# -----------------------------
_FALLBACK_TOP = [
    "BTCUSDT","ETHUSDT","BNBUSDT","XRPUSDT","SOLUSDT","ADAUSDT","DOGEUSDT","TRXUSDT",
    "AVAXUSDT","LINKUSDT","MATICUSDT","TONUSDT","SHIBUSDT","DOTUSDT","LTCUSDT","UNIUSDT",
    "BCHUSDT","ETCUSDT","APTUSDT","IMXUSDT","FILUSDT","NEARUSDT","OPUSDT","XLMUSDT",
    "HBARUSDT","INJUSDT","ARBUSDT","LDOUSDT","ATOMUSDT","STXUSDT","RNDRUSDT","MKRUSDT",
    "SUIUSDT","ALGOUSDT","AAVEUSDT","ICPUSDT","QNTUSDT","VETUSDT","GRTUSDT","PEPEUSDT",
    "FTMUSDT","MANAUSDT","SANDUSDT","AXSUSDT","FLOWUSDT","THETAUSDT","XTZUSDT","CHZUSDT",
    "RUNEUSDT","KAVAUSDT","ROSEUSDT","GMXUSDT","SEIUSDT","ARUSDT","TIAUSDT","TAOUSDT",
    "PYTHUSDT","ENAUSDT","JTOUSDT","JUPUSDT","FETUSDT","AGIXUSDT","OCEANUSDT","WLDUSDT",
    "ORDIUSDT","STRKUSDT","BLURUSDT","APEUSDT","BONKUSDT","DYDXUSDT","COMPUSDT","1INCHUSDT",
    "SFPUSDT","RAYUSDT","KSMUSDT","CFXUSDT","HNTUSDT","BALUSDT","CRVUSDT","ZECUSDT",
    "DASHUSDT","GMTUSDT","STORJUSDT","EWTUSDT","SKLUSDT","ZILUSDT","ICXUSDT","HOTUSDT",
    "WOOUSDT","CELOUSDT","IOTAUSDT","BATUSDT","SXPUSDT","GALAUSDT"
]

def _get_universe() -> List[str]:
    if SYMBOLS:
        return [s.strip().upper() for s in SYMBOLS]
    if cg_fetch_top_symbols is not None:
        try:
            top = cg_fetch_top_symbols(TOP_SYMBOLS)
            if isinstance(top, list) and top:
                return [s.strip().upper() for s in top]
        except Exception as e:
            print(f"⚠️ fetch_top_symbols indisponível: {e}")
    print("ℹ️ Usando lista estática de pares (fallback).")
    return _FALLBACK_TOP[:TOP_SYMBOLS]

# -----------------------------
# OHLC: coleta + normalização
# -----------------------------
def _fetch_any_ohlc(symbol: str, days: int) -> List:
    # 1) CoinGecko
    try:
        rows = cg_fetch_ohlc(symbol, days)
        if rows:
            return rows
    except Exception as e:
        print(f"⚠️ CoinGecko falhou {symbol}: {e}")
    # 2) CryptoCompare (se existir)
    if cc_fetch_ohlc is not None:
        try:
            rows = cc_fetch_ohlc(symbol, days)
            if rows:
                return rows
        except Exception as e:
            print(f"⚠️ CryptoCompare falhou {symbol}: {e}")
    return []

def _norm_ohlc(rows: List) -> List[Dict[str, float]]:
    """
    Converte para [{t,o,h,l,c}] e limpa dados ruins.
    Aceita [[ts,o,h,l,c], ...] ou [{...}] (open/high/low/close/outras chaves).
    """
    out: List[Dict[str,float]] = []
    if not rows:
        return out
    # Lista de listas
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        for r in rows:
            if len(r) >= 5:
                t, o, h, l, c = r[0], r[1], r[2], r[3], r[4]
                if None in (t,o,h,l,c): 
                    continue
                out.append({"t": float(t), "o": float(o), "h": float(h), "l": float(l), "c": float(c)})
    # Lista de dicts
    elif isinstance(rows, list) and isinstance(rows[0], dict):
        for r in rows:
            try:
                t = float(r.get("t", r.get("time", r.get("timestamp", 0.0))))
                o = float(r.get("o", r.get("open")))
                h = float(r.get("h", r.get("high")))
                l = float(r.get("l", r.get("low")))
                c = float(r.get("c", r.get("close")))
                if None in (t,o,h,l,c): 
                    continue
                out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
            except Exception:
                continue
    # Limpa NaN / inf / zeros absurdos
    clean: List[Dict[str,float]] = []
    for b in out:
        vals = [b["o"], b["h"], b["l"], b["c"]]
        if any(v is None or math.isnan(v) or math.isinf(v) for v in vals):
            continue
        # Se todos forem zero, ignora
        if all(abs(v) < 1e-12 for v in vals):
            continue
        clean.append(b)
    return clean

# -----------------------------
# Scoring/Mix
# -----------------------------
def _safe_score(ohlc_norm: List[Dict[str,float]]) -> float:
    try:
        s = score_signal(ohlc_norm)
        if isinstance(s, dict):
            s = float(s.get("score", s.get("value", s.get("confidence", 0.0))))
        elif isinstance(s, tuple):
            s = float(s[0])
        else:
            s = float(s)
        if s > 1.0:
            s /= 100.0
        return max(0.0, min(1.0, s))
    except Exception as e:
        print(f"❌ score_signal falhou: {e}")
        return 0.0

def _mix_conf(score_tech: float, ai_prob: Optional[float]) -> float:
    if WEIGHT_AI <= 0.0 or ai_prob is None:
        return score_tech
    tot = WEIGHT_TECH + WEIGHT_AI
    return max(0.0, min(1.0, (WEIGHT_TECH*score_tech + WEIGHT_AI*ai_prob) / max(tot,1e-9)))

# ==============================
# Pipeline
# ==============================
def run_pipeline():
    print(f"▶️ Runner iniciado. Intervalo = {RUN_INTERVAL_MIN} min.")
    print(f"NEWS ativo?: {USE_NEWS} | IA ativa?: {USE_AI} | Historico ativado?: True | Twitter ativo?: {USE_TWITTER}")
    print(f"Modelo disponível?: {has_model()} | Treino habilitado?: {TRAINING_ENABLED}")

    universe = _get_universe()
    if REMOVE_STABLES:
        before = len(universe)
        universe = [s for s in universe if not _is_stable_pair(s)]
        if before - len(universe) > 0:
            print(f"🧠 Removidos {before-len(universe)} pares estáveis redundantes.")

    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    collected: Dict[str, Any] = {}
    ok_syms: List[str] = []

    for sym in selected:
        print(f"Coletando OHLC {sym} (tf={DAYS_OHLC}d)…")
        try:
            raw = _fetch_any_ohlc(sym, DAYS_OHLC)
            norm = _norm_ohlc(raw)
            n = len(norm)
            if n < MIN_BARS:
                print(f"  ⚠️ {sym}: OHLC insuficiente após normalização ({n}/{MIN_BARS})")
                continue
            collected[sym] = norm
            ok_syms.append(sym)
            print(f"  → OK | candles={n}")
            # cache
            if not save_ohlc_cache(HISTORY_DIR, sym, norm):
                print(f"[HIST] falhou salvar cache {sym}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_syms:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # salva o bruto (normalizado) para debug
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_syms, "data": collected}, f, ensure_ascii=False)
        print(f"💾 Salvo {DATA_RAW_FILE} ({len(ok_syms)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha ao salvar {DATA_RAW_FILE}: {e}")

    saved = 0
    for sym in ok_syms:
        ohlc = collected[sym]

        # técnico
        score_tech = _safe_score(ohlc)

        # IA
        ai_prob = None
        if USE_AI and has_model():
            try:
                feats = {"score_tech": float(score_tech)}  # você pode enriquecer com features reais
                ai_prob = predict_proba(feats)  # 0..1
            except Exception as e:
                print(f"⚠️ IA indisponível: {e}")
                ai_prob = None

        final_conf = _mix_conf(score_tech, ai_prob)

        pct_tech = round(score_tech*100, 1)
        pct_ai   = "-" if ai_prob is None else f"{round(ai_prob*100,1)}%"
        pct_mix  = round(final_conf*100, 1)
        print(f"[IND] {sym} | Técnico: {pct_tech}% | IA: {pct_ai} | Mix(T:{WEIGHT_TECH},A:{WEIGHT_AI}): {pct_mix}% (min {int(MIN_CONFIDENCE*100)}%)")

        if score_tech < SCORE_THRESHOLD or final_conf < MIN_CONFIDENCE:
            continue

        # gera plano
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"⚠️ {sym}: erro generate_signal: {e}")
            sig = None
        if not isinstance(sig, dict):
            continue

        sig["symbol"]     = sym
        sig["confidence"] = float(final_conf)
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["strategy"]   = sig.get("strategy", "RSI+MACD+EMA+BB")
        sig["created_at"] = sig.get("created_at", _ts())
        if "id" not in sig:
            sig["id"] = f"sig-{int(time.time())}"

        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason}).")
            continue

        payload = {
            "symbol": sym,
            "entry_price": sig.get("entry"),
            "target_price": sig.get("tp"),
            "stop_loss": sig.get("sl"),
            "risk_reward": sig.get("rr", 2.0),
            "confidence_score": round(final_conf*100, 2),
            "strategy": sig.get("strategy"),
            "created_at": sig.get("created_at"),
            "id": sig.get("id"),
        }
        pushed = False
        try:
            pushed = send_signal_notification(payload)
        except Exception as e:
            print(f"⚠️ Falha no envio (notifier): {e}")
        print("✅ Notificado." if pushed else "❌ Falha no envio.")

        try:
            append_signal(sig)
            saved += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar {SIGNALS_FILE}: {e}")

    print(f"🗂 {saved} sinais salvos em {SIGNALS_FILE}")
    print(f"🕒 Fim: {_ts()}")

if __name__ == "__main__":
    run_pipeline()
