# -*- coding: utf-8 -*-
"""
notifier_trade_update.py — avisos de TP/SL para Telegram

- Lê TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID do ambiente
- Formata preços em USD (apenas campos de preço)
- Usa MarkdownV2 com escape seguro e fallback automático para HTML
- Retry com backoff para lidar com 429/erros transitórios

Uso:
    from notifier_trade_update import send_trade_update

    send_trade_update(
        symbol="BTCUSDT",
        status="TP",  # ou "SL" ou "CLOSE"
        exit_price=120_000.0,
        entry=118_000.0,
        tp=120_600.0,
        sl=117_300.0,
        rr=2.0,
        pnl_pct=+1.7,   # opcional
        signal_id="BTCUSDT-1755200567",
        created_at="2025-08-14 19:42:47 UTC",
        closed_at=None,  # se None usa agora (UTC)
    )
"""

import os
import time
import json
import requests
from decimal import Decimal, ROUND_DOWN
from datetime import datetime

# ---------------------------------------
# Config (env)
# ---------------------------------------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TG_URL    = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage" if BOT_TOKEN else None

DEFAULT_MAX_RETRIES = int(os.getenv("TG_MAX_RETRIES", "3"))
DEFAULT_RETRY_DELAY = float(os.getenv("TG_RETRY_DELAY", "2.0"))  # segundos

if not BOT_TOKEN:
    print("⚠️ TELEGRAM_BOT_TOKEN não definido.")
if not CHAT_ID:
    print("⚠️ TELEGRAM_CHAT_ID não definido.")


# ---------------------------------------
# Utils
# ---------------------------------------
def _utc_now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

# escape para MarkdownV2
_MD_V2_CHARS = [
    "\\", "_", "*", "[", "]", "(", ")", "~", "`", ">", "#",
    "+", "-", "=", "|", "{", "}", ".", "!"
]
def mdv2_escape(text: str) -> str:
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    for ch in _MD_V2_CHARS:
        text = text.replace(ch, "\\" + ch)
    return text

# formatação somente em PREÇOS
def fmt_price_usd(x) -> str:
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


# ---------------------------------------
# Builders
# ---------------------------------------
def _build_mdv2_update(payload: dict) -> str:
    """
    payload esperado:
      symbol, status ('TP'|'SL'|'CLOSE'), exit_price, entry, tp, sl, rr, pnl_pct (opcional),
      signal_id, created_at, closed_at
    """
    symbol     = mdv2_escape(payload.get("symbol", "—"))
    status     = (payload.get("status") or "CLOSE").upper()
    status_ico = "✅ TP" if status == "TP" else ("❌ SL" if status == "SL" else "ℹ️ CLOSE")

    entry      = fmt_price_usd(payload.get("entry", "—"))
    exit_price = fmt_price_usd(payload.get("exit_price", "—"))
    tp         = fmt_price_usd(payload.get("tp", "—"))
    sl         = fmt_price_usd(payload.get("sl", "—"))

    rr         = mdv2_escape(str(payload.get("rr", "—")))
    pnl_pct    = payload.get("pnl_pct", None)
    pnl_txt    = (("+" if float(pnl_pct) >= 0 else "") + f"{float(pnl_pct):.2f}%") if pnl_pct is not None else "—"

    signal_id  = mdv2_escape(payload.get("signal_id", "—"))
    created_at = mdv2_escape(payload.get("created_at", "—"))
    closed_at  = mdv2_escape(payload.get("closed_at", _utc_now_str()))

    msg = (
        f"🔔 *Atualização de trade* — *{symbol}*\n"
        f"{status_ico}\n"
        f"💵 *Saída:* `{exit_price}`\n"
        f"🟢 *Entrada:* `{entry}`\n"
        f"🎯 *Alvo:* `{tp}`\n"
        f"🛑 *Stop:* `{sl}`\n"
        f"📊 *R:R:* {rr} | *PnL:* {mdv2_escape(pnl_txt)}\n"
        f"📅 *Aberto:* {created_at}\n"
        f"📅 *Fechado:* {closed_at}\n"
        f"🆔 *ID:* {signal_id}"
    )
    return msg


def _build_html_update(payload: dict) -> str:
    symbol     = payload.get("symbol", "—")
    status     = (payload.get("status") or "CLOSE").upper()
    status_ico = "✅ TP" if status == "TP" else ("❌ SL" if status == "SL" else "ℹ️ CLOSE")

    entry      = fmt_price_usd(payload.get("entry", "—"))
    exit_price = fmt_price_usd(payload.get("exit_price", "—"))
    tp         = fmt_price_usd(payload.get("tp", "—"))
    sl         = fmt_price_usd(payload.get("sl", "—"))

    rr         = payload.get("rr", "—")
    pnl_pct    = payload.get("pnl_pct", None)
    pnl_txt    = (("+" if float(pnl_pct) >= 0 else "") + f"{float(pnl_pct):.2f}%") if pnl_pct is not None else "—"

    signal_id  = payload.get("signal_id", "—")
    created_at = payload.get("created_at", "—")
    closed_at  = payload.get("closed_at", _utc_now_str())

    msg = (
        f"🔔 <b>Atualização de trade</b> — <b>{symbol}</b><br>"
        f"{status_ico}<br>"
        f"💵 <b>Saída:</b> <code>{exit_price}</code><br>"
        f"🟢 <b>Entrada:</b> <code>{entry}</code><br>"
        f"🎯 <b>Alvo:</b> <code>{tp}</code><br>"
        f"🛑 <b>Stop:</b> <code>{sl}</code><br>"
        f"📊 <b>R:R:</b> {rr} | <b>PnL:</b> {pnl_txt}<br>"
        f"📅 <b>Aberto:</b> {created_at}<br>"
        f"📅 <b>Fechado:</b> {closed_at}<br>"
        f"🆔 <b>ID:</b> {signal_id}"
    )
    return msg


# ---------------------------------------
# POST com retry/backoff
# ---------------------------------------
def _post(payload: dict, parse_mode: str, max_retries: int, retry_delay: float) -> bool:
    if not TG_URL:
        print("❌ TG_URL ausente (provável BOT_TOKEN vazio).")
        return False

    attempt = 0
    delay = retry_delay
    while attempt < max_retries:
        attempt += 1
        try:
            body = dict(payload)
            body["parse_mode"] = parse_mode
            print(f"[TG] tentativa {attempt}, modo={parse_mode} …")
            r = requests.post(TG_URL, json=body, timeout=10)
            print(f"[TG] status={r.status_code}, resp={r.text[:200]}")

            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    return True
                # erro lógico (ex.: parse entities) — cai no fallback fora deste loop
                return False

            if r.status_code == 429:
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

            # outros erros
            if attempt < max_retries:
                print(f"⚠️ HTTP {r.status_code}. Retry em {delay}s …")
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


# ---------------------------------------
# Função pública
# ---------------------------------------
def send_trade_update(
    symbol: str,
    status: str,                 # 'TP' | 'SL' | 'CLOSE'
    exit_price,
    entry=None,
    tp=None,
    sl=None,
    rr=None,
    pnl_pct: float | None = None,
    signal_id: str | None = None,
    created_at: str | None = None,
    closed_at: str | None = None,
    max_retries: int | None = None,
    retry_delay: float | None = None,
) -> bool:
    """
    Envia uma atualização de trade (TP/SL/CLOSE) para o Telegram.
    Retorna True se enviado com sucesso (MDV2 ou fallback HTML).
    """
    if max_retries is None:
        max_retries = DEFAULT_MAX_RETRIES
    if retry_delay is None:
        retry_delay = DEFAULT_RETRY_DELAY

    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Telegram não configurado (BOT_TOKEN/CHAT_ID faltando).")
        return False

    payload = {
        "symbol": symbol,
        "status": (status or "CLOSE").upper(),
        "exit_price": exit_price,
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "pnl_pct": pnl_pct,
        "signal_id": signal_id or "",
        "created_at": created_at or "",
        "closed_at": closed_at or _utc_now_str(),
    }

    # 1) tenta em MarkdownV2
    md_text = _build_mdv2_update(payload)
    ok = _post({"chat_id": CHAT_ID, "text": md_text, "disable_web_page_preview": True},
               parse_mode="MarkdownV2",
               max_retries=max_retries,
               retry_delay=retry_delay)
    if ok:
        return True

    # 2) fallback HTML
    html_text = _build_html_update(payload)
    ok = _post({"chat_id": CHAT_ID, "text": html_text, "disable_web_page_preview": True},
               parse_mode="HTML",
               max_retries=max_retries,
               retry_delay=retry_delay)
    if ok:
        print("✅ Atualização enviada no fallback HTML.")
        return True

    print("❌ Falha ao enviar atualização de trade (MDV2 e HTML).")
    return False
