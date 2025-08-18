# -*- coding: utf-8 -*-
"""
notifier_telegram.py — envio de mensagens para Telegram
- Lê BOT e CHAT do ambiente (sem hardcode)
- Formata preços em USD (apenas entrada/alvo/stop)
- Tenta MarkdownV2 (com escape seguro) e faz fallback automático para HTML
- Retry + backoff para lidar com 429/erros transitórios
"""

import os
import time
import json
import requests
from decimal import Decimal, ROUND_DOWN

# --------------------------------------------------
# Config (via env)
# --------------------------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()  # pode ser @canal ou id numérico (-100xxxx)

DEFAULT_MAX_RETRIES = int(os.getenv("TG_MAX_RETRIES", "3"))
DEFAULT_RETRY_DELAY = float(os.getenv("TG_RETRY_DELAY", "2.0"))  # segundos

if not BOT_TOKEN:
    print("⚠️ TELEGRAM_BOT_TOKEN não definido.")
if not CHAT_ID:
    print("⚠️ TELEGRAM_CHAT_ID não definido.")

TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage" if BOT_TOKEN else None


# --------------------------------------------------
# Utils — formatação
# --------------------------------------------------
def fmt_price_usd(x) -> str:
    """
    Formata número como USD, sem notação científica. Casas dinâmicas:
    >= 1 -> 2 casas; >= 0.01 -> 4; >= 0.0001 -> 6; senão -> 8 casas.
    """
    try:
        d = Decimal(str(x))
        if d >= Decimal("1"):
            q = d.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        elif d >= Decimal("0.01"):
            q = d.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)
        elif d >= Decimal("0.0001"):
            q = d.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
        else:
            q = d.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
        s = format(q, "f")
        s = s.rstrip("0").rstrip(".") if "." in s else s
        return f"${s}"
    except Exception:
        try:
            x = float(x)
            return f"${x:.8f}".rstrip("0").rstrip(".")
        except Exception:
            return f"${x}"


# Escape seguro para MarkdownV2 (somente onde usamos MDV2)
_MD_V2_CHARS = [
    "\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#",
    "+", "-", "=", "|", "{", "}", ".", "!"
]
def mdv2_escape(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    for ch in _MD_V2_CHARS:
        text = text.replace(ch, "\\" + ch)
    return text


# --------------------------------------------------
# Montagem das mensagens
# --------------------------------------------------
def build_markdown_signal(content: dict) -> str:
    """
    Constrói mensagem em MarkdownV2 escapando o necessário.
    Apenas preços recebem formatação $... (sem escapes adicionais).
    """
    symbol           = mdv2_escape(content.get("symbol", "—"))
    entry_price      = fmt_price_usd(content.get("entry_price", "—"))
    target_price     = fmt_price_usd(content.get("target_price", "—"))
    stop_loss        = fmt_price_usd(content.get("stop_loss", "—"))
    risk_reward      = mdv2_escape(str(content.get("risk_reward", "—")))
    confidence_score = mdv2_escape(str(content.get("confidence_score", "—")))
    strategy         = mdv2_escape(content.get("strategy", "—"))
    created_at       = mdv2_escape(content.get("created_at", "—"))
    signal_id        = mdv2_escape(content.get("id", "—"))

    # OBS: não escapamos os preços (já estão formatados limpos, sem caracteres reservados),
    # e os colocamos dentro de `code` para ficar monoespaçado sem precisar de escapes.
    msg = (
        f"📢 *Novo sinal* para *{symbol}*\n"
        f"🎯 *Entrada:* `{entry_price}`\n"
        f"🎯 *Alvo:*   `{target_price}`\n"
        f"🛑 *Stop:*   `{stop_loss}`\n"
        f"📊 *R:R:* {risk_reward}\n"
        f"📈 *Confiança:* {confidence_score}%\n"
        f"🧠 *Estratégia:* {strategy}\n"
        f"📅 *Criado:* {created_at}\n"
        f"🆔 *ID:* {signal_id}"
    )
    return msg


def build_html_signal(content: dict) -> str:
    """
    Fallback em HTML — menos sensível a caracteres reservados.
    """
    symbol           = content.get("symbol", "—")
    entry_price      = fmt_price_usd(content.get("entry_price", "—"))
    target_price     = fmt_price_usd(content.get("target_price", "—"))
    stop_loss        = fmt_price_usd(content.get("stop_loss", "—"))
    risk_reward      = content.get("risk_reward", "—")
    confidence_score = content.get("confidence_score", "—")
    strategy         = content.get("strategy", "—")
    created_at       = content.get("created_at", "—")
    signal_id        = content.get("id", "—")

    # HTML simples (sem tags especiais nos números)
    msg = (
        f"📢 <b>Novo sinal</b> para <b>{symbol}</b><br>"
        f"🎯 <b>Entrada:</b> <code>{entry_price}</code><br>"
        f"🎯 <b>Alvo:</b>   <code>{target_price}</code><br>"
        f"🛑 <b>Stop:</b>   <code>{stop_loss}</code><br>"
        f"📊 <b>R:R:</b> {risk_reward}<br>"
        f"📈 <b>Confiança:</b> {confidence_score}%<br>"
        f"🧠 <b>Estratégia:</b> {strategy}<br>"
        f"📅 <b>Criado:</b> {created_at}<br>"
        f"🆔 <b>ID:</b> {signal_id}"
    )
    return msg


# --------------------------------------------------
# Envio
# --------------------------------------------------
def _post(payload: dict, parse_mode: str, max_retries: int, retry_delay: float) -> bool:
    """
    POST com retry/backoff. Retorna True se ok.
    """
    if not TG_URL:
        print("❌ TG_URL ausente (provável BOT_TOKEN vazio).")
        return False

    attempt = 0
    delay = retry_delay
    while attempt < max_retries:
        attempt += 1
        try:
            pld = dict(payload)
            pld["parse_mode"] = parse_mode
            print(f"[TG] tentativa {attempt}, modo={parse_mode} …")
            r = requests.post(TG_URL, json=pld, timeout=10)
            print(f"[TG] status={r.status_code}, resp={r.text[:200]}")

            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    return True

                # Caso erro sem ser 200-ok:
                desc = data.get("description", "")
                if "can't parse entities" in desc.lower():
                    # erro clássico de escape no MarkdownV2
                    return False
            elif r.status_code == 429:
                # rate limit — respeitar retry_after se vier
                retry_after = 0
                try:
                    retry_after = r.json().get("parameters", {}).get("retry_after", 0)
                except Exception:
                    pass
                if retry_after:
                    print(f"⚠️ 429: aguardando {retry_after}s …")
                    time.sleep(retry_after)
                else:
                    print(f"⚠️ 429: aguardando {delay}s …")
                    time.sleep(delay)
                    delay *= 2.0
                continue
            else:
                # outros erros HTTP
                if attempt < max_retries:
                    print(f"⚠️ Erro HTTP {r.status_code}. Retry em {delay}s …")
                    time.sleep(delay)
                    delay *= 2.0
                    continue
                return False

        except requests.exceptions.Timeout:
            if attempt < max_retries:
                print(f"⏰ Timeout. Retry em {delay}s …")
                time.sleep(delay)
                delay *= 2.0
                continue
            return False
        except requests.exceptions.RequestException as e:
            if attempt < max_retries:
                print(f"🌐 Erro de conexão: {e}. Retry em {delay}s …")
                time.sleep(delay)
                delay *= 2.0
                continue
            return False
    return False


def send_signal_notification(content, max_retries: int = None, retry_delay: float = None) -> bool:
    """
    Envia:
      - dict de sinal -> monta texto com preços em USD
      - str -> mensagem simples
    Tenta MarkdownV2 primeiro; se falhar por parse, faz fallback em HTML.
    """
    if max_retries is None:
        max_retries = DEFAULT_MAX_RETRIES
    if retry_delay is None:
        retry_delay = DEFAULT_RETRY_DELAY

    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Telegram não configurado (BOT_TOKEN/CHAT_ID faltando).")
        return False

    if isinstance(content, dict):
        # 1) tenta em MarkdownV2 (com escape)
        md_text = build_markdown_signal(content)
        payload = {"chat_id": CHAT_ID, "text": md_text, "disable_web_page_preview": True}
        ok = _post(payload, "MarkdownV2", max_retries, retry_delay)
        if ok:
            return True

        # 2) fallback HTML
        html_text = build_html_signal(content)
        payload = {"chat_id": CHAT_ID, "text": html_text, "disable_web_page_preview": True}
        ok = _post(payload, "HTML", max_retries, retry_delay)
        if ok:
            print("✅ Enviado no fallback HTML.")
            return True

        print("❌ Falha ao enviar sinal (MDV2 e HTML).")
        return False

    elif isinstance(content, str):
        # mensagem simples: tenta MarkdownV2 com escape básico
        md_text = mdv2_escape(content)
        payload = {"chat_id": CHAT_ID, "text": md_text}
        ok = _post(payload, "MarkdownV2", max_retries, retry_delay)
        if ok:
            return True

        # fallback HTML simples
        payload = {"chat_id": CHAT_ID, "text": content}
        ok = _post(payload, "HTML", max_retries, retry_delay)
        if ok:
            print("✅ Mensagem enviada no fallback HTML.")
            return True

        print("❌ Falha ao enviar mensagem simples.")
        return False

    else:
        print("❌ Tipo de conteúdo não suportado no notifier.")
        return False
