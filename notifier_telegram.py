# notifier_telegram.py
# Envio de mensagens para o Telegram com leitura robusta de variáveis de ambiente.
# Compatível com BOT_TOKEN/TELEGRAM_BOT_TOKEN e CHAT_ID/TELEGRAM_CHAT_ID.

from __future__ import annotations

import os
import time
import json
from typing import Iterable, Mapping, Any, Optional

import requests

# ----------------------------
# Leitura de ENV (tolerante)
# ----------------------------
def _env(name: str) -> Optional[str]:
    v = os.getenv(name)
    return v.strip() if isinstance(v, str) else v

def _env_bool(name: str, default: bool = True) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    raw = raw.lower()
    return raw not in {"0", "false", "no", "off", ""}

# aceita os dois padrões de nomes
BOT_TOKEN = _env("BOT_TOKEN") or _env("TELEGRAM_BOT_TOKEN")
CHAT_ID = _env("CHAT_ID") or _env("TELEGRAM_CHAT_ID")

# pode existir no .env (se não existir, assume True)
TELEGRAM_USE = _env_bool("TELEGRAM_USE", True)

# Timeout e tentativas
TG_TIMEOUT = float(os.getenv("TG_TIMEOUT", "15"))
TG_MAX_RETRY = int(os.getenv("TG_MAX_RETRY", "3"))
TG_RETRY_SLEEP = float(os.getenv("TG_RETRY_SLEEP", "1.5"))

def _enabled() -> bool:
    if not TELEGRAM_USE:
        _log("Telegram desativado por TELEGRAM_USE=false.")
        return False
    if not BOT_TOKEN or not CHAT_ID:
        _log("Telegram não configurado (BOT_TOKEN/CHAT_ID ausentes).")
        return False
    return True

def _api_url(method: str) -> str:
    return f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"

def _log(msg: str) -> None:
    # imprime algo visível nos logs, sem quebrar unicode
    try:
        print(f"[TG] {msg}", flush=True)
    except Exception:
        pass

# ----------------------------
# Envio básico de texto
# ----------------------------
def send_message(
    text: str,
    parse_mode: Optional[str] = "HTML",
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
) -> bool:
    """
    Envia uma mensagem de texto ao chat configurado.
    Retorna True se enviado, False caso contrário.
    """
    if not _enabled():
        return False

    payload = {
        "chat_id": CHAT_ID,
        "text": text if isinstance(text, str) else str(text),
        "disable_web_page_preview": disable_web_page_preview,
        "disable_notification": disable_notification,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    last_err = None
    for attempt in range(1, TG_MAX_RETRY + 1):
        try:
            r = requests.post(
                _api_url("sendMessage"),
                data=payload,
                timeout=TG_TIMEOUT,
            )
            if r.ok:
                return True
            # erros de rate limit / flood control
            if r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", TG_RETRY_SLEEP)
                _log(f"Rate limited. Aguardando {retry_after}s… (tentativa {attempt}/{TG_MAX_RETRY})")
                time.sleep(float(retry_after))
                continue
            # outros erros HTTP
            last_err = f"HTTP {r.status_code}: {r.text}"
            _log(f"Falha HTTP ao enviar: {last_err}")
        except Exception as e:
            last_err = repr(e)
            _log(f"Erro ao enviar: {last_err}")
        time.sleep(TG_RETRY_SLEEP)

    _log(f"Falha final ao enviar mensagem. Último erro: {last_err}")
    return False

# aliases comuns (para não quebrar chamadas antigas)
def send(text: str, **kwargs) -> bool:
    return send_message(text, **kwargs)

def notify(text: str, **kwargs) -> bool:
    return send_message(text, **kwargs)

def send_telegram_message(text: str, **kwargs) -> bool:
    return send_message(text, **kwargs)

# ----------------------------
# Helpers para enviar “sinais”
# ----------------------------
def format_signal_row(sig: Mapping[str, Any]) -> str:
    """
    Espera dict com chaves como: symbol, tech_score, ai_score, mix_score, reason etc.
    Monta uma linha legível para Telegram.
    """
    sym = sig.get("symbol", "?")
    t = sig.get("tech_score", sig.get("tech", sig.get("T", "")))
    a = sig.get("ai_score", sig.get("ai", sig.get("A", "")))
    m = sig.get("mix_score", sig.get("mix", sig.get("M", "")))
    reason = sig.get("reason", "")
    return f"<b>{sym}</b> | Técnico: <code>{t}</code> | IA: <code>{a}</code> | Mix: <b>{m}</b> {reason}"

def notify_signals(signals: Iterable[Mapping[str, Any]]) -> bool:
    """
    Envia uma lista de sinais agregados em uma única mensagem.
    """
    items = list(signals or [])
    if not items:
        return True  # nada a enviar, mas não é erro

    lines = ["<b>📣 Sinais gerados</b>"]
    for s in items:
        lines.append("• " + format_signal_row(s))

    return send_message("\n".join(lines), parse_mode="HTML")
