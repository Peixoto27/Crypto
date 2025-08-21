# notifier_telegram.py
import os
import requests
from dotenv import load_dotenv

# Carrega .env da pasta atual
load_dotenv()

# Aceita tanto TELEGRAM_* quanto os nomes curtos BOT_TOKEN / CHAT_ID
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("CHAT_ID")

API_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage" if TOKEN else None

def _enabled() -> bool:
    return bool(TOKEN and CHAT_ID)

def send_message(text: str, parse_mode: str | None = "Markdown", disable_web_page_preview: bool = True) -> bool:
    """Envia uma mensagem simples via Telegram."""
    if not _enabled():
        print("[TG] Telegram desativado (BOT_TOKEN/CHAT_ID faltando).")
        return False
    try:
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        r = requests.post(API_URL, data=payload, timeout=15)
        ok = r.status_code == 200 and r.json().get("ok", False)
        if ok:
            print("[TG] Mensagem enviada.")
        else:
            print(f"[TG] Falha ({r.status_code}): {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[TG] Erro ao enviar: {e}")
        return False

def send_signal_notification(sig) -> bool:
    """
    Envia notificação formatada de 1 sinal (ou lista de sinais).
    Espera dict com chaves como: symbol, action, mix_score, tech_score, ai_score, price.
    """
    if isinstance(sig, (list, tuple)):
        ok = True
        for s in sig:
            ok = send_signal_notification(s) and ok
        return ok

    symbol = (sig or {}).get("symbol") or (sig or {}).get("pair") or "????"
    action = (sig or {}).get("action") or "BUY"
    direction = "🟢 COMPRA" if action.upper().startswith("B") else "🔴 VENDA"

    mix = (sig or {}).get("mix_score")
    tech = (sig or {}).get("tech_score")
    ai   = (sig or {}).get("ai_score")
    price = (sig or {}).get("price")

    parts = [
        f"*{direction} {symbol}*",
        f"Mix: {mix:.1f}%" if isinstance(mix, (int, float)) else None,
        f"Técnico: {tech:.1f}%" if isinstance(tech, (int, float)) else None,
        f"IA: {ai:.1f}%" if isinstance(ai, (int, float)) else None,
        f"Preço: {price}" if price is not None else None,
    ]
    text = "\n".join(p for p in parts if p)

    return send_message(text)
