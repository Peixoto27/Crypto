# -*- coding: utf-8 -*-
"""
main.py — pipeline completo (técnico + IA tech-only)

Fluxo:
1) Seleção de universo (SYMBOLS fixo ou tudo que estiver definido no seu env)
2) Coleta OHLC (prioriza CryptoCompare se disponível; fallback para CoinGecko)
3) Salva data_raw.json e cache por símbolo (history/ohlc)
4) Score técnico (apply_strategies.score_signal)
5) IA opcional: extrai features e faz predict_proba (LightGBM/LogReg)
6) Mix (TECH_WEIGHT / AI_WEIGHT) -> confiança final
7) Gera sinal (apply_strategies.generate_signal), anti-duplicados e envia Telegram
8) Alimenta dataset da IA (append_snapshot + update_labels + autotrain)

Requisitos: apply_strategies.py, positions_manager.py, notifier_telegram.py, signal_generator.py,
history_manager.py (para salvar OHLC por símbolo)
"""

import os, json, time
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional

# -------- Config ----------
def _b(s: str) -> bool:
    return os.getenv(s, "false").lower() in ("1","true","yes")

# Módulos do projeto existentes
from positions_manager import should_send_and_register
from signal_generator import append_signal
from notifier_telegram import send_signal_notification
from history_manager import save_ohlc_cache  # assume save_ohlc_cache(symbol, bars)

# Pontuação técnica (já existente no seu projeto)
from apply_strategies import score_signal, generate_signal

# IA tech-only
import model_manager as mm

# Preferência de fonte de OHLC
USE_CRYPTOCOMPARE  = _b("USE_CRYPTOCOMPARE") or _b("USE_CC") or _b("USE_CRYPTOCOMPARE")
CC_TIMEFRAME       = os.getenv("CC_TIMEFRAME", "1h")
CC_LIMIT           = int(os.getenv("CC_LIMIT", "180"))

DAYS_OHLC          = int(os.getenv("DAYS_OHLC", "30"))
MIN_BARS           = int(os.getenv("MIN_BARS", "60"))
DATA_RAW_FILE      = os.getenv("DATA_RAW_FILE", "data_raw.json")
HISTORY_DIR        = os.getenv("HISTORY_DIR", "data/history")

# Universo
SYMBOLS            = [s for s in os.getenv("SYMBOLS", "").replace(" ", "").split(",") if s]
SELECT_PER_CYCLE   = int(os.getenv("SELECT_PER_CYCLE", str(len(SYMBOLS) if SYMBOLS else 100)))

# Mix & thresholds
TECH_WEIGHT        = float(os.getenv("TECH_WEIGHT", "1.0"))
AI_WEIGHT          = float(os.getenv("AI_WEIGHT",   "1.0"))
MIX_MIN_THRESHOLD  = float(os.getenv("MIX_MIN_THRESHOLD", "70")) / 100.0  # em 0..1

# Anti-duplicados
COOLDOWN_HOURS     = float(os.getenv("COOLDOWN_HOURS", "6"))
CHANGE_THRESHOLD_PCT = float(os.getenv("CHANGE_THRESHOLD_PCT", "1.0"))

# Flags de logging/status
AI_ENABLE          = _b("AI_ENABLE") or _b("USE_AI")
NEWS_ACTIVE        = _b("NEWS_USE") or _b("USE_NEWS") or _b("USE_RSS_NEW") or _b("USE_THENEWSAPI")
TW_ACTIVE          = _b("TWITTER_USE")
SAVE_HISTORY       = _b("SAVE_HISTORY")

LOG_ASCII          = _b("LOG_ASCII")

# ---------- Fetchers (tentamos CC, senão CG) ----------
_fetch_cc = None
try:
    # esperado: fetch_ohlc_cc(symbol, timeframe="1h", limit=180) -> [[ts,o,h,l,c], ...]
    from data_fetcher_cryptocompare import fetch_ohlc_cc as _fetch_cc
except Exception:
    _fetch_cc = None

_fetch_cg = None
try:
    # esperado: fetch_ohlc(symbol, days=30) -> [[ts,o,h,l,c], ...]
    from data_fetcher_coingecko import fetch_ohlc as _fetch_cg
except Exception:
    _fetch_cg = None

def _norm_rows(raw):
    out = []
    if not raw:
        return out
    if isinstance(raw, list) and raw and isinstance(raw[0], list):
        for r in raw:
            if len(r) >= 5:
                out.append({"t": float(r[0]), "o": float(r[1]), "h": float(r[2]),
                            "l": float(r[3]), "c": float(r[4])})
    elif isinstance(raw, list) and isinstance(raw[0], dict):
        for r in raw:
            t = float(r.get("t", r.get("time", 0.0)))
            o = float(r.get("o", r.get("open", 0.0)))
            h = float(r.get("h", r.get("high", 0.0)))
            l = float(r.get("l", r.get("low", 0.0)))
            c = float(r.get("c", r.get("close", 0.0)))
            out.append({"t": t, "o": o, "h": h, "l": l, "c": c})
    return out

def _fetch_ohlc_any(symbol: str) -> List[Dict[str, float]]:
    # 1) CryptoCompare
    if USE_CRYPTOCOMPARE and _fetch_cc:
        try:
            raw = _fetch_cc(symbol, timeframe=CC_TIMEFRAME, limit=CC_LIMIT)
            rows = _norm_rows(raw)
            if len(rows) >= MIN_BARS:
                return rows
        except Exception as e:
            print(f"[DATA] CC falhou {symbol}: {e}")

    # 2) CoinGecko
    if _fetch_cg:
        try:
            raw = _fetch_cg(symbol, DAYS_OHLC)
            rows = _norm_rows(raw)
            return rows
        except Exception as e:
            print(f"[DATA] CG falhou {symbol}: {e}")

    return []

# --------------- Helpers ---------------
def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _safe_score(ohlc) -> float:
    try:
        res = score_signal(ohlc)
        if isinstance(res, (list, tuple)):
            s = float(res[0])
        elif isinstance(res, dict):
            s = float(res.get("score", res.get("value", res.get("confidence", 0.0))))
        else:
            s = float(res)
    except Exception:
        s = 0.0
    if s > 1.0:
        s = s / 100.0
    return max(0.0, min(1.0, s))

def _mix(tech: float, ai_proba: Optional[float]) -> float:
    if ai_proba is None or AI_WEIGHT <= 1e-9:
        return tech
    tw = max(1e-9, TECH_WEIGHT)
    aw = max(1e-9, AI_WEIGHT)
    return max(0.0, min(1.0, (tw*tech + aw*ai_proba) / (tw+aw)))

# --------------- Pipeline ---------------
def run_pipeline():
    print(f"Runner iniciado. Intervalo = {os.getenv('RUN_INTERVAL_MIN','20')} min.")
    print(f"NEWS ativo?: {NEWS_ACTIVE} | IA ativa?: {AI_ENABLE} | Historico ativado?: {SAVE_HISTORY} | Twitter ativo?: {TW_ACTIVE}")

    universe = SYMBOLS[:] if SYMBOLS else []
    if not universe:
        print("⚠️ SYMBOLS vazio. Defina pares no .env (SYMBOLS=BTCUSDT,ETHUSDT,...)")
        return

    selected = universe[:SELECT_PER_CYCLE]
    print(f"Moedas deste ciclo ({len(selected)}/{len(universe)}): {', '.join(selected[:10])}{'...' if len(selected)>10 else ''}")

    collected: Dict[str, Any] = {}
    ok_syms: List[str] = []

    for sym in selected:
        print(f"Coletando OHLC {sym} (tf={CC_TIMEFRAME if USE_CRYPTOCOMPARE else f'{DAYS_OHLC}d'}, limit={CC_LIMIT if USE_CRYPTOCOMPARE else 'n/a'})...")
        rows = _fetch_ohlc_any(sym)
        if len(rows) < MIN_BARS:
            print(f"  -> OHLC insuficiente ({len(rows)}/{MIN_BARS})")
            continue
        print(f"  -> OK | candles={len(rows)}")
        collected[sym] = rows
        ok_syms.append(sym)

        # salva cache por símbolo (p/ IA rotular depois)
        if SAVE_HISTORY:
            try:
                save_ohlc_cache(sym, rows, base_dir=HISTORY_DIR)
            except Exception as e:
                print(f"[HIST] falhou salvar cache {sym}: {e}")

    if not ok_syms:
        print("Nenhum ativo com OHLC suficiente.")
        return

    # salva data_raw.json
    try:
        with open(DATA_RAW_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ok_syms, "data": collected}, f, ensure_ascii=False)
        print(f"Salvo {DATA_RAW_FILE} ({len(ok_syms)} ativos)")
    except Exception as e:
        print(f"Falha ao salvar {DATA_RAW_FILE}: {e}")

    # Carrega modelo (se IA habilitada)
    model_pack = mm.load_or_none() if AI_ENABLE else None
    if AI_ENABLE and model_pack is None:
        print("[AI] Modelo não encontrado (ainda). Vou apenas registrar amostras p/ treino.")

    saved_count = 0
    for sym in ok_syms:
        ohlc = collected[sym]

        # 1) Score técnico
        tech = _safe_score(ohlc)

        # 2) Features + AI proba (se houver)
        ai_proba = None
        if AI_ENABLE and model_pack is not None:
            feats = mm.extract_features_from_ohlc(ohlc)
            if feats is not None:
                ai_proba = mm.predict_proba_single(model_pack, feats)

        mixed = _mix(tech, ai_proba)

        # logs
        pct_ai = f"{round((ai_proba or 0.0)*100,1)}%"
        print(f"[IND] {sym} | Técnico: {round(tech*100,1)}% | IA: {pct_ai} | Mix(T:{TECH_WEIGHT},A:{AI_WEIGHT}): {round(mixed*100,1)}% (min {int(MIX_MIN_THRESHOLD*100)}%)")

        # 3) Gera sinal se passou do limiar
        if mixed < MIX_MIN_THRESHOLD:
            # Mesmo quando não gera sinal, aproveita para alimentar dataset
            mm.append_snapshot_for_training(sym, ohlc)
            continue

        # Geração do plano (entry/tp/sl) usando sua estratégia existente
        sig = None
        try:
            sig = generate_signal(ohlc)
        except Exception as e:
            print(f"{sym}: erro em generate_signal: {e}")
            sig = None

        if not isinstance(sig, dict):
            mm.append_snapshot_for_training(sym, ohlc)
            continue

        # completa campos do sinal
        sig["symbol"]     = sym
        sig["confidence"] = float(mixed)
        sig["rr"]         = float(sig.get("rr", 2.0))
        sig["strategy"]   = sig.get("strategy", "TECH+AI")
        sig["created_at"] = sig.get("created_at", _ts())
        sig["id"]         = sig.get("id", f"{sym}-{int(time.time())}")

        # anti-duplicado
        ok_send, reason = should_send_and_register(
            {"symbol": sym, "entry": sig.get("entry"), "tp": sig.get("tp"), "sl": sig.get("sl")},
            cooldown_hours=COOLDOWN_HOURS,
            change_threshold_pct=CHANGE_THRESHOLD_PCT
        )
        if not ok_send:
            print(f"{sym} não enviado ({reason}).")
            mm.append_snapshot_for_training(sym, ohlc)
            continue

        # envia Telegram
        pushed = False
        try:
            pushed = send_signal_notification({
                "symbol": sym,
                "entry_price": sig.get("entry"),
                "target_price": sig.get("tp"),
                "stop_loss": sig.get("sl"),
                "risk_reward": sig.get("rr", 2.0),
                "confidence_score": round(mixed * 100, 2),
                "strategy": sig.get("strategy"),
                "created_at": sig.get("created_at"),
                "id": sig.get("id"),
            })
        except Exception as e:
            print(f"Falha no notifier: {e}")

        if pushed:
            print("Notificação enviada.")
        else:
            print("Falha no envio (ver notifier).")

        # salva registro do sinal
        try:
            append_signal(sig)
            saved_count += 1
        except Exception as e:
            print(f"Erro ao salvar sinal: {e}")

        # alimenta dataset com snapshot também
        mm.append_snapshot_for_training(sym, ohlc)

    print(f"{saved_count} sinais salvos em {os.getenv('SIGNALS_FILE','signals.json')}")

    # 4) Atualiza labels e (opcional) treina
    try:
        done, blanks = mm.update_labels()
        print(f"[AI] Labels atualizados: {done} (pendentes ainda: {max(0, blanks-done)})")
    except Exception as e:
        print(f"[AI] update_labels falhou: {e}")

    if AI_ENABLE and (os.getenv("AI_AUTOTRAIN","false").lower() in ("1","true","yes")):
        try:
            mm.train_and_save()
        except Exception as e:
            print(f"[AI] Treino automático falhou: {e}")

    print(f"Fim: {_ts()}")


if __name__ == "__main__":
    run_pipeline()
