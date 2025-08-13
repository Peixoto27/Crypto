# -*- coding: utf-8 -*-
"""
notifier_telegram.py
Envio de mensagens de sinais para Telegram (canal/grupo/DM) usando a Bot API.

Config via .env:
- TELEGRAM_BOT_TOKEN=8158...NE4M
- TELEGRAM_CHAT_ID=-1002897426078   # (ou @seu_canal)

Uso:
from notifier_telegram import send_signal_notification
send_signal_notification({...})
"""

import os
import time
import requests
from typing import Any, Dict

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ---------- helpers ----------
def _short(v, n=120):
    try:
        s = str(v)
        return s if len(s) <= n else s[:n-1] + "…"
    except Exception:
        return str(v)

def _build_text_html(content: Dict[str, Any]) -> str:
    """
    Monta mensagem em HTML (parse_mode="HTML").
    Aceita dicionário no formato do pipeline.
    """
    symbol = content.get("symbol", "N/A")
    entry  = content.get("entry_price", content.get("entry"))
    tp     = content.get("target_price", content.get("tp"))
    sl     = content.get("stop_loss", content.get("sl"))
    rr     = content.get("risk_reward")
    conf   = content.get("confidence_score", content.get("confidence"))
    if conf is not None:
        try:
            conf = float(conf)
            if conf <= 1.0:  # se veio normalizado
                conf *= 100.0
            conf = round(conf, 2)
        except Exception:
            pass
    strat  = content.get("strategy", "RSI+MACD+EMA+BB")
    created= content.get("created_at", content.get("timestamp"))
    sig_id = content.get("id", "")
    ai_pb  = content.get("ai_proba")
    if ai_pb is not None:
        try:
            ai_pb = float(ai_pb)
            if ai_pb <= 1.0:
                ai_pb *= 100.0
            ai_pb = round(ai_pb, 1)
        except Exception:
            pass

    header = "🧠 <b>IA ATIVA</b> — " if ai_pb is not None else ""
    lines = [
        f"{header}📢 <b>Novo sinal</b> para <b>{symbol}</b>",
        f"🎯 <b>Entrada:</b> <code>{_short(entry, 32)}</code>",
        f"🎯 <b>Alvo:</b>   <code>{_short(tp, 32)}</code>",
        f"🛑 <b>Stop:</b>   <code>{_short(sl, 32)}</code>",
    ]
    if rr is not None:
        lines.append(f"📊 <b>R:R:</b> <code>{_short(rr, 16)}</code>")
    if conf is not None:
        lines.append(f"📈 <b>Confiança:</b> <code>{conf}%</code>")
    if ai_pb is not None:
        lines.append(f"🧠 <b>IA (proba):</b> <code>{ai_pb}%</code>")

    lines.append(f"🧠 <b>Estratégia:</b> <code>{_short(strat, 64)}</code>")
    if created is not None:
        lines.append(f"📅 <b>Criado:</b> <code>{_short(created, 48)}</code>")
    if sig_id:
        lines.append(f"🆔 <b>ID:</b> <code>{_short(sig_id, 48)}</code>")

    return "\n".join(lines)

def _post_json(method: str, payload: Dict[str, Any], max_retries=3, retry_delay=2) -> bool:
    """
    POST com retry/backoff e tratamento de 429 (rate limit).
    """
    url = f"{API_URL}/{method}"
    delay = float(retry_delay)
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=15)
            try:
                body = r.json()
            except Exception:
                body = {"text": r.text[:200]}
            print(f"[TG] tentativa {attempt}, status={r.status_code}, resp={str(body)[:200]}")

            if r.status_code == 200 and body.get("ok"):
                return True

            # Rate limit
            if r.status_code == 429:
                ra = body.get("parameters", {}).get("retry_after", delay)
                try:
                    ra = float(ra)
                except Exception:
                    ra = delay
                print(f"[TG] 429 rate limit: aguardando {ra}s…")
                time.sleep(ra)
                continue

            # Outras falhas: backoff exponencial
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
        except requests.RequestException as e:
            print(f"[TG] erro de rede: {e}")
            if attempt < max_retries:
                time.sleep(delay)
                delay *= 2
    return False

# ---------- API pública ----------
def send_signal_notification(content: Any, max_retries=3, retry_delay=2) -> bool:
    """
    Envia:
      - dict de sinal formatado em HTML
      - str (mensagem simples)
    Retorna True/False.
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env")
        return False

    # 1) Monta texto
    if isinstance(content, dict):
        text_html = _build_text_html(content)
    else:
        text_html = str(content)

    # 2) Tenta em HTML
    payload_html = {
        "chat_id": CHAT_ID,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    ok = _post_json("sendMessage", payload_html, max_retries=max_retries, retry_delay=retry_delay)
    if ok:
        print("✅ Enviado no HTML.")
        return True

    # 3) Fallback: sem parse_mode (texto puro)
    print("⚠️ Tentando fallback (texto puro)…")
    payload_plain = {
        "chat_id": CHAT_ID,
        "text": text_html,
        "disable_web_page_preview": True,
    }
    ok2 = _post_json("sendMessage", payload_plain, max_retries=max_retries, retry_delay=retry_delay)
    if ok2:
        print("✅ Enviado no fallback texto puro.")
        return True

    print("❌ Falha ao enviar mensagem após retries e fallback.")
    return False
