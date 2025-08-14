# -*- coding: utf-8 -*-
"""
auto_labeler.py
Fecha automaticamente posições abertas usando OHLC recente.
- Lê positions.json (open/closed)
- Lê data_raw.json (OHLC por símbolo)
- Se HIGH >= TP => 'hit_tp'; se LOW <= SL => 'hit_sl'
- Atualiza positions.json e history.json
- Opcional: envia mensagem no Telegram

Requer: notifier_telegram.send_signal_notification(str) já existente
"""

import os
import json
from datetime import datetime, timezone
from math import ceil

# ---- Env / Arquivos ----
POSITIONS_FILE    = os.getenv("POSITIONS_FILE", "positions.json")
HISTORY_FILE      = os.getenv("HISTORY_FILE", "history.json")
DATA_RAW_FILE     = os.getenv("DATA_RAW_FILE", "data_raw.json")

AUTO_LABEL_ENABLED   = os.getenv("AUTO_LABEL_ENABLED", "true").lower() == "true"
LABEL_LOOKBACK_HOURS = float(os.getenv("LABEL_LOOKBACK_HOURS", "24"))   # quantas horas olhar para trás
LABEL_METHOD         = os.getenv("LABEL_METHOD", "wick").lower()        # "wick" (high/low) ou "close"
LABEL_SEND_SUMMARY   = os.getenv("LABEL_SEND_SUMMARY", "true").lower() == "true"

try:
    from positions_manager import close_position
except Exception:
    # fallback caso o módulo mude de nome no seu repo
    def close_position(symbol: str, reason: str):
        return False

# usa o seu notifier existente (aceita str)
try:
    from notifier_telegram import send_signal_notification as notify
except Exception:
    def notify(msg):  # no-op se não houver
        print(f"[AUTO_LABEL] (notify mock) {msg}")
        return False


# ---------- Utils ----------
def _utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _parse_created_at(s: str) -> float:
    # esperado "YYYY-MM-DD HH:MM:SS UTC"
    try:
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return 0.0


# ---------- Núcleo ----------
def _infer_candle_hours(ohlc):
    """
    Tenta inferir o timeframe das velas em horas a partir dos timestamps (ms).
    Retorna None se não conseguir.
    """
    try:
        if not ohlc or len(ohlc) < 2:
            return None
        t0 = ohlc[0][0]  # ms
        t1 = ohlc[1][0]
        delta_h = (t1 - t0) / 1000.0 / 3600.0
        if delta_h <= 0:
            return None
        return round(delta_h, 4)
    except Exception:
        return None


def _hit_tp(c, tp, method):
    # c = [ts, open, high, low, close]
    if method == "close":
        return float(c[4]) >= float(tp)
    return float(c[2]) >= float(tp)  # wick (HIGH)

def _hit_sl(c, sl, method):
    if method == "close":
        return float(c[4]) <= float(sl)
    return float(c[3]) <= float(sl)  # wick (LOW)


def auto_close_by_ohlc():
    """
    Varre posições abertas e fecha por TP/SL com base no OHLC recente.
    """
    if not AUTO_LABEL_ENABLED:
        print("🕹️ AUTO_LABEL desativado (AUTO_LABEL_ENABLED=false).")
        return {"closed": 0, "wins": 0, "losses": 0}

    book = _load_json(POSITIONS_FILE, {"open": [], "closed": []})
    open_list = [p for p in book.get("open", []) if p.get("status", "open") == "open"]
    if not open_list:
        print("🗃️ AUTO_LABEL: sem posições abertas.")
        return {"closed": 0, "wins": 0, "losses": 0}

    data_raw = _load_json(DATA_RAW_FILE, {})
    history  = _load_json(HISTORY_FILE, [])

    closed_count = wins = losses = 0

    for pos in list(open_list):
        sym = pos.get("symbol")
        entry = pos.get("entry")
        tp = pos.get("tp")
        sl = pos.get("sl")
        created_at = pos.get("created_at", "")

        if not sym or tp is None or sl is None:
            continue

        sym_ohlc = data_raw.get(sym, {}).get("ohlc") or data_raw.get(sym)  # compatibilidade
        if not sym_ohlc:
            print(f"ℹ️ AUTO_LABEL: sem OHLC para {sym}")
            continue

        # timeframe/hours por vela
        tf_h = _infer_candle_hours(sym_ohlc) or 4.0  # fallback 4h
        lookback_candles = max(1, ceil(LABEL_LOOKBACK_HOURS / tf_h))

        # filtra últimas N velas
        recent = sym_ohlc[-lookback_candles:]

        # opcional: só considera velas APÓS o created_at
        created_ts = _parse_created_at(created_at)
        if created_ts > 0:
            recent = [c for c in recent if (c[0] / 1000.0) >= created_ts]

        # varre procurando SL/TP
        outcome = None
        when_ts = None
        for c in recent:
            if _hit_sl(c, sl, LABEL_METHOD):
                outcome = "hit_sl"
                when_ts = c[0]
                break
            if _hit_tp(c, tp, LABEL_METHOD):
                outcome = "hit_tp"
                when_ts = c[0]
                break

        if not outcome:
            continue

        # fecha posição no positions.json
        ok = close_position(sym, outcome)
        closed_count += 1
        if outcome == "hit_tp":
            wins += 1
        else:
            losses += 1

        # grava no history.json
        closed_at_str = datetime.fromtimestamp(when_ts / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC") if when_ts else _utcnow_str()
        hist_item = {
            "id": pos.get("id") or f"{sym}-{int(created_ts)}",
            "symbol": sym,
            "entry": entry,
            "target_price": tp,
            "stop_loss": sl,
            "created_at": created_at,
            "closed_at": closed_at_str,
            "outcome": "win" if outcome == "hit_tp" else "loss",
            "reason": outcome,
        }
        history.append(hist_item)

        # notifica no Telegram
        try:
            side_emoji = "✅" if outcome == "hit_tp" else "🛑"
            msg = (
                f"{side_emoji} {sym} | {('ALVO atingido' if outcome=='hit_tp' else 'STOP acionado')}\n"
                f"• Entrada: {entry}\n"
                f"• Alvo: {tp}\n"
                f"• Stop: {sl}\n"
                f"• Fechado em: {closed_at_str}\n"
            )
            notify(msg)
        except Exception as e:
            print(f"[AUTO_LABEL] erro ao notificar: {e}")

    # salva history atualizado
    _save_json(HISTORY_FILE, history)

    if LABEL_SEND_SUMMARY and closed_count:
        msg = f"📘 Auto-label: {closed_count} fechado(s) | ✅ {wins} | 🛑 {losses}"
        try:
            notify(msg)
        except Exception:
            pass

    print(f"📘 AUTO_LABEL resumo: closed={closed_count}, wins={wins}, losses={losses}")
    return {"closed": closed_count, "wins": wins, "losses": losses}
