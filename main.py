# -*- coding: utf-8 -*-
"""
main.py — pipeline principal (estável)
- Seleciona universo de moedas (fixo via env ou dinâmico via CoinGecko)
- Rotaciona LOTES por ciclo (cursor em scan_state.json)
- Coleta OHLC (CoinGecko e fallback CryptoCompare se disponível)
- Calcula score técnico
- (Opcional) mistura com IA se houver modelo carregado
- Gera plano (entry/tp/sl), evita duplicados, notifica no Telegram
- Salva caches OHLC por símbolo e data_raw.json para debug
"""

import os
import json
import time
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# -----------------------------
# Fetchers de mercado
# -----------------------------
from data_fetcher_coingecko import fetch_ohlc as cg_fetch_ohlc, fetch_top_symbols
try:
    # opcional: só será usado se o arquivo existir no projeto
    from data_fetcher_cryptocompare import fetch_ohlc_cc as cc_fetch_ohlc
except Exception:
    cc_fetch_ohlc = None

# -----------------------------
# Estratégia / Notificador / De-duplicação / Persistência
# -----------------------------
from apply_strategies import score_signal, generate_signal
from notifier_telegram import send_signal_notification
from positions_manager import should_send_and_register
from signal_generator import append_signal

# -----------------------------
# Histórico / IA (opcional)
# -----------------------------
from history_manager import save_ohlc_cache
try:
    from model_manager import predict_proba, has_model
except Exception:
    def predict_proba(_: Dict[str, float]) -> Optional[float]:
        return None
    def has_model() -> bool:
        return False

# ==============================
# Config via Environment
# ==============================
def _as_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y")

RUN_INTERVAL_MIN   = os.getenv("RUN_INTERVAL_MIN", "20")

# Universo
SYMBOLS            = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
TOP_SYMBOLS        = int(os.getenv("TOP_SYMBOLS", "100"))
SELECT_PER_CYCLE   = int(os.getenv("SELECT_PER_CYCLE", "8"))

# Coleta / Qualidade
DAYS_OHLC          = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS           = int(os.getenv("MIN_BARS", "180"))

# Limiar técnico e final
SCORE_THRESHOLD    = float(os.getenv("SCORE_THRESHOLD", "0.70"))  # 0..1
MIN_CONFIDENCE     = float(os.getenv("MIN_CONFIDENCE", "0.70"))   # 0..1

# Pesos Técnico x IA (sem sentimento)
WEIGHT_TECH        = float(os.getenv("WEIGHT_TECH", "1.0"))
WEIGHT_AI          = float(os.getenv("WEIGHT_AI", "0.0"))          # 0 = ignora IA

# Anti-duplicados
COOLDOWN_HOURS       = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# Arquivos
DATA_RAW_FILE      = os.getenv("DATA_RAW_FILE", "data_raw.json")
HISTORY_DIR        = os.getenv("HISTORY_DIR", "data/history")
CURSOR_FILE        = os.getenv("CURSOR_FILE", "scan_state.json")
SIGNALS_FILE       = os.getenv("SIGNALS_FILE", "signals.json")

# Logs de recursos (apenas exibição de status)
USE_NEWS           = _as_bool("USE_RSS_NEW", "false") or _as_bool("USE_THENEWSAPI", "false")
USE_TWITTER        = _as_bool("USE_TWITTER", "false")
USE_AI             = _as_bool("USE_AI", "true")
TRAINING_ENABLED   = _as_bool("TRAINING_ENABLED", "true")

# Opcional: remover pares estáveis redundantes
REMOVE_STABLES     = _as_bool("REMOVE_STABLES", "true")
STABLE_SUFFIXES    = ("USDT", "FDUSD", "USDC", "BUSD", "TUSD")

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
    batch = []
    for i in range(min(take, len(symbols))):
        batch.append(symbols[(off + i) % len(symbols)])
    st["offset"] = (off + take) % len(symbols)
    st["cycle"] = int(st.get("cycle", 0)) + 1
    _save_cursor(st)
    return batch

def _safe_score(ohlc) -> float:
    """
    Aceita: float 0..1, tuple(score,...), dict {"score":0..1} ou percentual >1
    """
    try:
        s = score_signal(ohlc)
        if isinstance(s, tuple):
            s = float(s[0])
        elif isinstance(s, dict):
            s = float(s.get("score", s.get("value", s.get("confidence", 0.0))))
        else:
            s = float(s)
        if s > 1.0:  # veio em %
            s /= 100.0
    except Exception:
        s = 0.0
    return max(0.0, min(1.0, s))

def _mix_conf(score_tech: float, ai_prob: Optional[float]) -> float:
    """
    Combina técnico (0..1) com IA (0..1). Se IA não existir, retorna técnico.
    """
    if WEIGHT_AI <= 0.0 or ai_prob is None:
        return score_tech
    total = WEIGHT_TECH + WEIGHT_AI
    return max(0.0, min(1.0, (WEIGHT_TECH * score_tech + WEIGHT_AI * ai_prob) / max(total, 1e-9)))

def _is_stable_pair(symbol: str) -> bool:
    """Remove pares exóticos de estáveis (ex.: FDUSDUSDT)."""
    if not REMOVE_STABLES:
        return False
    cleaned = symbol.upper()
    # Ex.: FDUSDUSDT termina em USDT e começa com um estável -> redundante
    for suf in STABLE_SUFFIXES:
        if cleaned.endswith(suf):
            base = cleaned[:-len(suf)]
            for s2 in STABLE_SUFFIXES:
                if base.endswith(s2):
                    return True
    return False

# -----------------------------
# Coleta OHLC com fallback
# -----------------------------
def _fetch_any_ohlc(symbol: str, days: int) -> List:
    """
    Tenta provedores na ordem:
      1) CoinGecko
      2) CryptoCompare (se disponível)
    Retorna lista de candles no formato do fetcher (mantemos como veio).
    """
    # 1) CoinGecko
    try:
        rows = cg_fetch_ohlc(symbol, days)
        if rows and len(rows) > 0:
            return rows
    except Exception as e:
        print(f"⚠️ CoinGecko falhou {symbol}: {e}")

    # 2) CryptoCompare (se implementado no projeto)
    if cc_fetch_ohlc is not None:
        try:
            rows = cc_fetch_ohlc(symbol, days)
            if rows and len(rows) > 0:
                return rows
        except Exception as e:
            print(f"⚠️ CryptoCompare falhou {symbol}: {e}")

    return []

# ==============================
# Pipeline principal
# ==============================
def run_pipeline():
    print(f"▶️ Runner iniciado. Intervalo = {RUN_INTERVAL_MIN} min.")
    print(f"NEWS ativo?: {USE_NEWS} | IA ativa?: {USE_AI} | Historico ativado?: True | Twitter ativo?: {USE_TWITTER}")
    print(f"Modelo disponível?: {has_model()} | Treino habilitado?: {TRAINING_ENABLED}")

    # Universo
    if SYMBOLS:
        universe = SYMBOLS[:]
    else:
        universe = fetch_top_symbols(TOP_SYMBOLS)

    # Remoção opcional de pares estáveis redundantes
    if REMOVE_STABLES:
        before = len(universe)
        universe = [s for s in universe if not _is_stable_pair(s)]
        removed = before - len(universe)
        if removed > 0:
            print(f"🧠 Removidos {removed} pares estáveis redundantes.")

    selected = _rotate(universe, SELECT_PER_CYCLE)
    print(f"Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected)}")

    collected: Dict[str, Any] = {}
    ok_syms: List[str] = []

    # Coleta
    for sym in selected:
        print(f"Coletando OHLC {sym} (tf={DAYS_OHLC}d, limit=n/a)...")
        try:
            rows = _fetch_any_ohlc(sym, DAYS_OHLC)
            n = len(rows) if rows else 0
            if n < MIN_BARS:
                print(f"  ⚠️ {sym}: OHLC insuficiente ({n}/{MIN_BARS})")
                continue
            collected[sym] = rows
            ok_syms.append(sym)
            print(f"  -> OK | candles={n}")

            # salva cache OHLC por símbolo
            if not save_ohlc_cache(HISTORY_DIR, sym, rows):
                print(f"[HIST] falhou salvar cache {sym}")
        except Exception as e:
            print(f"⚠️ Erro OHLC {sym}: {e}")

    if not ok_syms:
        print("❌ Nenhum ativo com OHLC suficiente.")
        return

    # Salva raw para debug
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_syms, "data": collected}, f, ensure_ascii=False)
        print(f"Salvo {DATA_RAW_FILE} ({len(ok_syms)} ativos)")
    except Exception as e:
        print(f"⚠️ Falha salvando {DATA_RAW_FILE}: {e}")

    # Scoring + IA + geração + envio
    saved = 0
    for sym in ok_syms:
        ohlc = collected[sym]

        # técnico
        score_tech = _safe_score(ohlc)

        # IA (se houver modelo e uso ativado)
        ai_prob = None
        if USE_AI and has_model():
            try:
                # feature mínima; ajuste conforme seu trainer
                feats = {"score_tech": float(score_tech)}
                ai_prob = predict_proba(feats)  # 0..1
            except Exception:
                ai_prob = None

        final_conf = _mix_conf(score_tech, ai_prob)

        pct_tech = round(score_tech * 100, 1)
        pct_ai   = "-" if ai_prob is None else f"{round(ai_prob * 100, 1)}%"
        pct_mix  = round(final_conf * 100, 1)
        print(f"[IND] {sym} | Técnico: {pct_tech}% | IA: {pct_ai} | Mix(T:{WEIGHT_TECH},A:{WEIGHT_AI}): {pct_mix}% (min {int(MIN_CONFIDENCE*100)}%)")

        # filtros
        if score_tech < SCORE_THRESHOLD or final_conf < MIN_CONFIDENCE:
            continue

        # sinal
        try:
            sig = generate_signal(ohlc)  # dict com entry/tp/sl/rr/strategy...
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

        # anti-duplicado
        ok_to_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_to_send:
            print(f"🟡 {sym} não enviado ({reason}).")
            continue

        # Telegram
        payload = {
            "symbol": sym,
            "entry_price": sig.get("entry"),
            "target_price": sig.get("tp"),
            "stop_loss": sig.get("sl"),
            "risk_reward": sig.get("rr", 2.0),
            "confidence_score": round(final_conf * 100, 2),
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

        # Persistência do sinal
        try:
            append_signal(sig)
            saved += 1
        except Exception as e:
            print(f"⚠️ Erro ao salvar {SIGNALS_FILE}: {e}")

    print(f"{saved} sinais salvos em {SIGNALS_FILE}")
    print(f"Fim: {_ts()}")

# -----------------------------
if __name__ == "__main__":
    run_pipeline()
