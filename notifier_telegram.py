# notifier_telegram.py
import os
import json
import requests
from typing import Any, Dict, Iterable, Optional, Union

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else None

def _esc_md(s: str) -> str:
    """Escapa caracteres do Markdown v2 do Telegram."""
    # usamos Markdown "clássico" em captions curtas; se trocar para MarkdownV2, descomente abaixo
    return s

def send_message(
    text: str,
    parse_mode: Optional[str] = "Markdown",
    disable_web_page_preview: bool = True,
    disable_notification: bool = False,
) -> bool:
    if not API_BASE or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"{API_BASE}/sendMessage",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
                "disable_notification": disable_notification,
            },
            timeout=15,
        )
        return r.ok
    except Exception:
        return False

def send_photo(
    photo_url: str,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    disable_notification: bool = False,
) -> bool:
    """Envia uma imagem por URL (sem baixar localmente)."""
    if not API_BASE or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"{API_BASE}/sendPhoto",
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "photo": photo_url,
                "caption": caption or "",
                "parse_mode": parse_mode,
                "disable_notification": disable_notification,
            },
            timeout=20,
        )
        return r.ok
    except Exception:
        return False

# ---------- CARD/GRÁFICO BONITO ----------
def _chart_url(symbol: str, mix: Optional[float], tech: Optional[float], ai: Optional[float]) -> str:
    """Gera uma URL do QuickChart com barras de Mix/Técnico/IA."""
    def nz(x):
        return float(x) if isinstance(x, (int, float)) else None

    mix = nz(mix) or 0.0
    tech = nz(tech) or 0.0
    ai = nz(ai) or 0.0

    # Config do gráfico (QuickChart usa chart.js)
    cfg = {
        "type": "bar",
        "data": {
            "labels": ["Mix", "Técnico", "IA"],
            "datasets": [
                {
                    "label": "Confiança (%)",
                    "data": [round(mix,1), round(tech,1), round(ai,1)],
                    # sem cores explícitas pra manter simples; QuickChart usa padrão
                }
            ],
        },
        "options": {
            "plugins": {
                "legend": {"display": False},
                "title": {"display": True, "text": f"{symbol} • Sinal"},
            },
            "scales": {
                "y": {"min": 0, "max": 100, "ticks": {"stepSize": 20}}
            },
        },
        "backgroundColor": "transparent",
    }
    params = {
        "width": 700,
        "height": 430,
        "format": "png",
        "version": "4",
        "backgroundColor": "white",
        "chart": json.dumps(cfg, separators=(",", ":")),
    }
    # Monta URL
    from urllib.parse import urlencode
    return "https://quickchart.io/chart?" + urlencode(params)

def _direction_and_emoji(action: str) -> str:
    a = (action or "").upper()
    if a.startswith("S"):  # SELL / SHORT
        return "🔴 VENDA"
    return "🟢 COMPRA"

def _fmt_number(x: Any) -> str:
    try:
        if isinstance(x, int):
            return f"{x}"
        return f"{float(x):,.4f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(x)

# ---------- API PÚBLICA USADA PELO SISTEMA ----------
Signal = Union[Dict[str, Any], Iterable[Dict[str, Any]]]

def send_signal_notification(sig: Signal) -> bool:
    """
    Envia notificação detalhada.
    - Se conseguir gerar a imagem do card, envia com `send_photo`.
    - Se der qualquer erro, faz fallback para `send_message` com texto.
    """
    if sig is None:
        return False

    # Lista -> manda cada uma
    if isinstance(sig, (list, tuple)):
        ok = True
        for s in sig:
            ok = send_signal_notification(s) and ok
        return ok

    # Dicionário de um sinal
    symbol = (sig.get("symbol") or sig.get("pair") or "????").upper()
    action = sig.get("action") or ("BUY" if (sig.get("side") or "").upper() != "SELL" else "SELL")
    direction = _direction_and_emoji(action)

    mix  = sig.get("mix") or sig.get("score_mix") or sig.get("mix_score")
    tech = sig.get("tech") or sig.get("score_tech") or sig.get("technical")
    ai   = sig.get("ai") or sig.get("score_ai") or sig.get("ml") or sig.get("prob")

    price  = sig.get("price") or sig.get("close") or sig.get("last") or None
    tf     = sig.get("tf") or sig.get("timeframe") or "30d"
    source = sig.get("source") or sig.get("origin") or "bot"

    # Monta caption (texto da imagem)
    lines = [
        f"*{direction} {symbol}*",
        f"Timeframe: `{tf}`",
    ]
    if isinstance(mix, (int, float)):  lines.append(f"Mix: *{mix:.1f}%*")
    if isinstance(tech, (int, float)): lines.append(f"Técnico: *{tech:.1f}%*")
    if isinstance(ai, (int, float)):   lines.append(f"IA: *{ai:.1f}%*")
    if price is not None:               lines.append(f"Preço: `{_fmt_number(price)}`")
    lines.append(f"_origem:_ `{source}`")

    caption = "\n".join(lines)

    # tenta enviar imagem (gráfico de barras)
    try:
        img_url = _chart_url(symbol, mix, tech, ai)
        if send_photo(img_url, caption=caption, parse_mode="Markdown"):
            return True
    except Exception:
        pass

    # fallback: texto puro
    text = caption
    return send_message(text, parse_mode="Markdown")
