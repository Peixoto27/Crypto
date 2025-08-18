# -*- coding: utf-8 -*-
"""
notifier_v2.py — central de notificações
- Sinal novo: envia via notifier_telegram
- Fechamento (TP/SL/Close): varre HISTORY_FILE e envia via notifier_trade_update
- Evita duplicatas com um "seen set" salvo em notified_updates.json

Integração sugerida no main.py:
    from history_manager import evaluate_pending_outcomes
    from notifier_v2 import notify_new_signal, monitor_and_notify_closures

    # quando gerar um novo sinal:
    notify_new_signal(payload_dict)

    # ao final do ciclo:
    evaluate_pending_outcomes(lookahead_hours=int(os.getenv("AUTO_LABEL_LOOKAHEAD_HOURS","48")))
    monitor_and_notify_closures()
"""

import os
import json
from datetime import datetime
from typing import Dict, Any, List, Set

# módulos existentes do seu projeto
try:
    from notifier_telegram import send_signal_notification
except Exception:
    send_signal_notification = None

try:
    from notifier_trade_update import send_trade_update
except Exception:
    send_trade_update = None

# ============== Config (.env / Variables) ==================
HISTORY_FILE = os.getenv("HISTORY_FILE", "history.json")  # onde o history_manager grava
NOTIFIED_DB  = os.getenv("NOTIFIED_UPDATES_FILE", "notified_updates.json")  # ids já notificados (TP/SL/CLOSE)

# ===========================================================
def _now_utc_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _load_json_list(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def _load_json_set(path: str) -> Set[str]:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return set(data)
        if isinstance(data, dict) and "ids" in data and isinstance(data["ids"], list):
            return set(data["ids"])
        return set()
    except Exception:
        return set()

def _save_json_set(path: str, items: Set[str]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(items)), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Falha ao salvar {path}: {e}")

# ===========================================================
# API pública
# ===========================================================
def notify_new_signal(content: Dict[str, Any]) -> bool:
    """
    Encaminha o payload do novo sinal para o notifier_telegram.
    'content' deve conter: symbol, entry_price, target_price, stop_loss, rr, confidence_score, strategy, created_at, id
    """
    if send_signal_notification is None:
        print("❌ notifier_telegram.send_signal_notification indisponível.")
        return False
    try:
        ok = send_signal_notification(content)
        if ok:
            print("✅ Sinal inicial notificado (notifier_v2).")
        else:
            print("❌ Falha ao notificar sinal inicial (notifier_v2).")
        return ok
    except Exception as e:
        print(f"❌ Erro ao notificar sinal inicial: {e}")
        return False


def monitor_and_notify_closures() -> Dict[str, int]:
    """
    Lê o HISTORY_FILE, encontra sinais que deixaram de ser 'open'
    e envia atualização (TP/SL/CLOSE) uma única vez por id.
    Retorna um sumário com contagens.
    """
    summary = {"checked": 0, "sent_tp": 0, "sent_sl": 0, "sent_close": 0, "skipped_dup": 0, "errors": 0}

    if send_trade_update is None:
        print("❌ notifier_trade_update.send_trade_update indisponível.")
        return summary

    history = _load_json_list(HISTORY_FILE)
    seen = _load_json_set(NOTIFIED_DB)

    if not history:
        print(f"ℹ️ Sem histórico para varrer ({HISTORY_FILE}).")
        return summary

    # Normalizar para lista
    if isinstance(history, dict) and "signals" in history:
        records = history.get("signals", [])
    else:
        records = history  # assume lista direta

    for rec in records:
        try:
            summary["checked"] += 1
            sig_id  = str(rec.get("id") or "")
            label   = (rec.get("label") or rec.get("status") or "open").lower()
            symbol  = rec.get("symbol") or rec.get("pair") or "—"

            # só notificar quando saiu de 'open'
            if label in ("open", "", None):
                continue

            if not sig_id:
                # sem id não conseguimos deduplicar
                print(f"⚠️ Registro sem ID ignorado (symbol={symbol}, label={label}).")
                continue

            if sig_id in seen:
                summary["skipped_dup"] += 1
                continue

            # mapear status e preço de saída
            if label == "hit_tp":
                status = "TP"
                exit_price = rec.get("exit_price", rec.get("tp", None))
            elif label == "hit_sl":
                status = "SL"
                exit_price = rec.get("exit_price", rec.get("sl", None))
            else:
                status = "CLOSE"  # expired, manual, etc.
                exit_price = rec.get("exit_price", rec.get("last_close", rec.get("entry", None)))

            entry = rec.get("entry")
            tp    = rec.get("tp")
            sl    = rec.get("sl")
            rr    = rec.get("rr", 2.0)
            pnl   = rec.get("pnl_pct", None)
            created_at = rec.get("created_at") or rec.get("timestamp")
            closed_at  = rec.get("closed_at")  or _now_utc_str()

            # dispara o aviso
            ok = send_trade_update(
                symbol=symbol,
                status=status,
                exit_price=exit_price,
                entry=entry,
                tp=tp,
                sl=sl,
                rr=rr,
                pnl_pct=pnl,
                signal_id=sig_id,
                created_at=created_at,
                closed_at=closed_at
            )

            if ok:
                seen.add(sig_id)
                if status == "TP":
                    summary["sent_tp"] += 1
                elif status == "SL":
                    summary["sent_sl"] += 1
                else:
                    summary["sent_close"] += 1
            else:
                summary["errors"] += 1

        except Exception as e:
            print(f"❌ Erro ao processar histórico: {e}")
            summary["errors"] += 1

    # salva o set de ids notificados
    _save_json_set(NOTIFIED_DB, seen)

    print(
        f"🔁 Monitor fechamento — "
        f"checados: {summary['checked']} | TP: {summary['sent_tp']} | SL: {summary['sent_sl']} | "
        f"Close: {summary['sent_close']} | duplicados: {summary['skipped_dup']} | erros: {summary['errors']}"
    )
    return summary
