# -*- coding: utf-8 -*-
"""
report_weekly.py — resumo semanal dos sinais enviados.

O que faz:
  - Lê signals.json
  - Filtra últimos 7 dias
  - Calcula: total, por símbolo, confiança média, top estratégias
  - Envia um resumo no Telegram (Markdown)

Env esperados:
  - TELEGRAM_BOT_TOKEN  (ou TELEGRAMA_BOT_TOKEN)
  - TELEGRAM_CHAT_ID    (ou ID_DE_CHAT_DO_TELEGRAM)
  - SIGNALS_FILE (padrão 'signals.json')
"""

import os
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import urllib.request
import urllib.parse

SIGNALS_FILE = os.getenv("SIGNALS_FILE", "signals.json")

def _ts() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

def _load_signals() -> List[Dict[str, Any]]:
    try:
        with open(SIGNALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            return []
    except Exception:
        return []

def _parse_when(s: str) -> datetime:
    # aceita "YYYY-mm-dd HH:MM:SS UTC" ou iso
    try:
        if "UTC" in s:
            return datetime.strptime(s.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.utcnow()

def _fmt_pct(x: float) -> str:
    return f"{round(x*100,1)}%"

def _get_telegram():
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAMA_BOT_TOKEN")
    chat  = os.getenv("TELEGRAM_CHAT_ID")   or os.getenv("ID_DE_CHAT_DO_TELEGRAM")
    if not token or not chat:
        return None, None
    return token, chat

def _send_telegram_markdown(text: str) -> bool:
    token, chat = _get_telegram()
    if not token or not chat:
        print("⚠️ Telegram não configurado (bot/chat).")
        return False
    base = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(base, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                return True
    except Exception as e:
        print(f"⚠️ Falha Telegram: {e}")
    return False

def run_weekly_report():
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    sigs = _load_signals()
    recent = [s for s in sigs if _parse_when(str(s.get("created_at", _ts()))) >= week_ago]

    total = len(recent)
    if total == 0:
        msg = f"*📅 Relatório semanal — {_ts()}*\n\nSem sinais nos últimos 7 dias."
        _send_telegram_markdown(msg)
        print(msg)
        return

    by_symbol: Dict[str, List[Dict[str, Any]]] = {}
    by_strategy: Dict[str, int] = {}
    confs = []

    for s in recent:
        sym = s.get("symbol", "?")
        by_symbol.setdefault(sym, []).append(s)
        strat = s.get("strategy", "N/A")
        by_strategy[strat] = by_strategy.get(strat, 0) + 1
        try:
            confs.append(float(s.get("confidence", 0.0)))
        except Exception:
            pass

    avg_conf = sum(confs)/len(confs) if confs else 0.0
    top_syms = sorted(by_symbol.items(), key=lambda kv: len(kv[1]), reverse=True)[:5]
    top_strats = sorted(by_strategy.items(), key=lambda kv: kv[1], reverse=True)[:5]

    lines = []
    lines.append(f"*📅 Relatório semanal — {_ts()}*")
    lines.append(f"Total de sinais: *{total}* | Confiança média: *{_fmt_pct(avg_conf)}*")
    lines.append("")
    lines.append("*Top símbolos:*")
    for sym, lst in top_syms:
        cavg = sum(float(x.get("confidence", 0.0)) for x in lst)/len(lst)
        lines.append(f"• `{sym}` — {len(lst)} sinais (conf. média {_fmt_pct(cavg)})")
    lines.append("")
    lines.append("*Top estratégias:*")
    for strat, n in top_strats:
        lines.append(f"• {strat} — {n} sinais")

    text = "\n".join(lines)
    ok = _send_telegram_markdown(text)
    if ok:
        print("✅ Relatório semanal enviado ao Telegram.")
    else:
        print("⚠️ Não foi possível enviar ao Telegram. Conteúdo:")
        print(text)


if __name__ == "__main__":
    run_weekly_report()
