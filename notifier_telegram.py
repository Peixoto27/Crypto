# -*- coding: utf-8 -*-
"""
notifier_telegram.py — v2 (cards coloridos + Entrada/Alvo/Stop em $)
- Envia imagem (quando possível) + legenda em HTML
- Fallback automático para texto se algo falhar
- Calcula TP/SL se não vierem no sinal (TP_PCT / SL_PCT)

ENV necessários:
  TELEGRAM_BOT_TOKEN
  TELEGRAM_CHAT_ID
Opcional:
  TP_PCT=0.03    -> 3% sobre a entrada (alvo)
  SL_PCT=0.02    -> 2% sobre a entrada (stop)
  TG_TIMEOUT=10  -> timeout por request
  TG_RETRIES=3   -> tentativas
"""

from __future__ import annotations
import os
import io
import time
import json
import math
import requests
from typing import Optional, Tuple

# PIL é opcional: se não existir, seguimos com texto apenas
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# =================================
# Config
# =================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TP_PCT    = float(os.getenv("TP_PCT", "0.03"))     # 3% alvo (fallback)
SL_PCT    = float(os.getenv("SL_PCT", "0.02"))     # 2% stop (fallback)
TG_TIMEOUT= int(float(os.getenv("TG_TIMEOUT", "10")))
TG_RETRIES= int(float(os.getenv("TG_RETRIES", "3")))

# cores
GREEN = (46, 204, 113)      # compra
RED   = (220, 68, 55)       # venda
WHITE = (245, 245, 245)
BLACK = (12, 12, 12)
GRAY  = (170, 170, 170)

# =================================
# Utils
# =================================
def _dir_emoji_label_color(action: Optional[str]) -> Tuple[str, str, tuple, bool]:
    """
    Retorna (emoji, rótulo, cor, is_sell) a partir da ação.
    Padrão: BUY (compra).
    """
    a = (action or "BUY").upper()
    is_sell = a.startswith("S") or a == "SELL" or a == "SHORT"
    return (
        ('🔴', 'VENDA', RED, True) if is_sell
        else ('🟢', 'COMPRA', GREEN, False)
    )

def _fmt_price_usd(x: Optional[float]) -> Optional[str]:
    """
    Formata preço com $, adaptando casas decimais conforme magnitude.
    """
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return str(x)
    # casas: >1000 (2), >1 (2), >0.1 (4), >0.01 (5), senão 8
    if v >= 1000: d = 2
    elif v >= 1:  d = 2
    elif v >= 0.1: d = 4
    elif v >= 0.01: d = 5
    else: d = 8
    return f"${v:,.{d}f}"

def _pct_rel(to_value: Optional[float], from_value: Optional[float]) -> Optional[float]:
    try:
        tv = float(to_value)
        fv = float(from_value)
        if fv == 0: return None
        return (tv / fv - 1.0) * 100.0
    except Exception:
        return None

def _entry_target_stop(sig: dict, is_sell: bool) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Pega entry/target/stop do sinal; se faltarem, calcula com TP_PCT/SL_PCT.
    Aceita chaves alternativas: entry_price/target_price/stop_loss ou price/last_price.
    """
    entry = (
        sig.get("entry") or sig.get("entry_price") or
        sig.get("price") or sig.get("last_price")
    )
    tp = sig.get("target") or sig.get("target_price") or sig.get("tp")
    sl = sig.get("stop")   or sig.get("stop_loss")    or sig.get("sl")

    if entry is not None:
        try:
            entry = float(entry)
            if tp is None:
                tp = entry * (1 - TP_PCT) if is_sell else entry * (1 + TP_PCT)
            else:
                tp = float(tp)
            if sl is None:
                sl = entry * (1 + SL_PCT) if is_sell else entry * (1 - SL_PCT)
            else:
                sl = float(sl)
        except Exception:
            # se deu ruim na conversão, melhor zerar tudo pra não confundir
            return None, None, None
    return entry, tp, sl

def _escape_html(text: str) -> str:
    if not text: return ""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

def _post_telegram(method: str, payload: dict, files: Optional[dict]=None) -> requests.Response:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    return requests.post(url, data=payload if files else json.dumps(payload),
                         files=files,
                         headers={} if files else {"Content-Type": "application/json"},
                         timeout=TG_TIMEOUT)

# =================================
# Card (imagem) — opcional
# =================================
def _load_font(size: int) -> Optional[ImageFont.FreeTypeFont]:
    # tenta usar DejaVuSans (comum em containers), senão fonte padrão
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except Exception:
            try:
                return ImageFont.load_default()
            except Exception:
                return None

def _draw_text(draw, xy, text, font, fill):
    try:
        draw.text(xy, text, font=font, fill=fill)
    except Exception:
        # em último caso, desenha com parâmetros mínimos
        draw.text(xy, text, fill=fill)

def _build_card_image(signal: dict) -> Optional[bytes]:
    """
    Monta uma imagem simples com cabeçalho colorido e infos de preço.
    Se PIL indisponível, retorna None para forçar fallback em texto.
    """
    if not _PIL_OK:
        return None

    emoji, rotulo, main_color, is_sell = _dir_emoji_label_color(signal.get("action"))
    symbol = signal.get("symbol", "—")

    entry, target, stop = _entry_target_stop(signal, is_sell)

    w, h = 1080, 540
    img = Image.new("RGB", (w, h), color=BLACK)
    draw = ImageDraw.Draw(img)

    # barra superior colorida
    bar_h = 88
    draw.rectangle([(0, 0), (w, bar_h)], fill=main_color)

    # fontes
    f_big  = _load_font(46)
    f_med  = _load_font(34)
    f_small= _load_font(28)

    # título
    title = f"{emoji} {rotulo} {symbol}"
    _draw_text(draw, (32, 20), title, f_big, WHITE)

    # infos
    y0 = bar_h + 24
    lines = []

    if entry is not None:
        lines.append(f"📈 Entrada: {_fmt_price_usd(entry)}")
    if target is not None and entry is not None:
        p = _pct_rel(target, entry)
        lines.append(f"🎯 Alvo: {_fmt_price_usd(target)}" + (f" ({p:+.2f}%)" if p is not None else ""))
    if stop is not None and entry is not None:
        p = _pct_rel(stop, entry)
        lines.append(f"🛡️ Stop: {_fmt_price_usd(stop)}" + (f" ({p:+.2f}%)" if p is not None else ""))

    # extras (se existirem no seu payload)
    rr = signal.get("risk_reward") or signal.get("rr")
    conf = signal.get("confidence_score") or signal.get("confidence")
    strat = signal.get("strategy")
    created = signal.get("created_at")
    sid = signal.get("id")

    if rr is not None:
        lines.append(f"📊 R:R: {rr}")
    if conf is not None:
        try:
            conf = float(conf)
            # normaliza se veio 0..1
            if conf <= 1.0:
                conf *= 100.0
            lines.append(f"🔎 Confiança: {conf:.2f}%")
        except Exception:
            lines.append(f"🔎 Confiança: {conf}")
    if strat:
        lines.append(f"🧠 Estratégia: {strat}")
    if created:
        lines.append(f"📅 Criado: {created}")
    if sid:
        lines.append(f"🆔 ID: {sid}")

    y = y0
    for ln in lines:
        _draw_text(draw, (32, y), ln, f_med, WHITE)
        y += 42

    # rodapé sutil
    _draw_text(draw, (32, h - 36), "Cripton Signals • auto-generated", f_small, GRAY)

    # exporta
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

# =================================
# Mensagem (HTML)
# =================================
def _build_html_caption(signal: dict) -> str:
    emoji, rotulo, main_color, is_sell = _dir_emoji_label_color(signal.get("action"))
    sym = _escape_html(signal.get("symbol", "—"))

    entry, target, stop = _entry_target_stop(signal, is_sell)

    linha = []
    if entry is not None:
        linha.append(f"📈 Entrada: {_escape_html(_fmt_price_usd(entry))}")
    if target is not None and entry is not None:
        p = _pct_rel(target, entry)
        add = f" ({p:+.2f}%)" if p is not None else ""
        linha.append(f"🎯 Alvo: {_escape_html(_fmt_price_usd(target))}{_escape_html(add)}")
    if stop is not None and entry is not None:
        p = _pct_rel(stop, entry)
        add = f" ({p:+.2f}%)" if p is not None else ""
        linha.append(f"🛡️ Stop: {_escape_html(_fmt_price_usd(stop))}{_escape_html(add)}")

    rr = signal.get("risk_reward") or signal.get("rr")
    conf = signal.get("confidence_score") or signal.get("confidence")
    strat = signal.get("strategy")
    created = signal.get("created_at")
    sid = signal.get("id")

    info2 = []
    if rr is not None:
        info2.append(f"📊 R:R: {_escape_html(str(rr))}")
    if conf is not None:
        try:
            cf = float(conf)
            if cf <= 1.0: cf *= 100.0
            info2.append(f"🔎 Confiança: {cf:.2f}%")
        except Exception:
            info2.append(f"🔎 Confiança: {_escape_html(str(conf))}")
    if strat:
        info2.append(f"🧠 Estratégia: {_escape_html(str(strat))}")
    if created:
        info2.append(f"📅 Criado: {_escape_html(str(created))}")
    if sid:
        info2.append(f"🆔 ID: {_escape_html(str(sid))}")

    head = f"{emoji} <b>{rotulo}</b> <b>{sym}</b>"
    sub  = " • ".join(linha)
    sub2 = " • ".join(info2)

    parts = [head]
    if sub:  parts.append(sub)
    if sub2: parts.append(sub2)
    return "\n".join(parts)

# =================================
# Envio
# =================================
def _send_html_text(caption: str) -> bool:
    payload = {"chat_id": CHAT_ID, "text": caption, "parse_mode": "HTML", "disable_web_page_preview": True}
    for attempt in range(1, TG_RETRIES+1):
        try:
            r = _post_telegram("sendMessage", payload)
            if r.status_code == 200 and r.json().get("ok"):
                print("✅ Enviado (texto HTML).")
                return True
            elif r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 3)
                print(f"⚠️ Rate limit. Aguardando {retry_after}s…")
                time.sleep(float(retry_after))
            else:
                print(f"❌ Erro sendMessage: {r.status_code} {r.text}")
        except requests.exceptions.RequestException as e:
            print(f"🌐 Erro rede (texto) tent.{attempt}: {e}")
        if attempt < TG_RETRIES:
            time.sleep(1.5 * attempt)
    return False

def _send_photo_with_caption(png_bytes: bytes, caption: str) -> bool:
    payload = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
    files = {"photo": ("card.png", png_bytes, "image/png")}
    for attempt in range(1, TG_RETRIES+1):
        try:
            r = _post_telegram("sendPhoto", payload, files=files)
            if r.status_code == 200 and r.json().get("ok"):
                print("✅ Enviado com imagem.")
                return True
            elif r.status_code == 429:
                retry_after = r.json().get("parameters", {}).get("retry_after", 3)
                print(f"⚠️ Rate limit. Aguardando {retry_after}s…")
                time.sleep(float(retry_after))
            else:
                print(f"❌ Erro sendPhoto: {r.status_code} {r.text}")
        except requests.exceptions.RequestException as e:
            print(f"🌐 Erro rede (foto) tent.{attempt}: {e}")
        if attempt < TG_RETRIES:
            time.sleep(1.5 * attempt)
    return False

# =================================
# API pública
# =================================
def send_signal_notification(content) -> bool:
    """
    Aceita:
      - dict (sinal) com campos conhecidos do projeto
      - str  (mensagem livre)
    Envia imagem + HTML quando possível; fallback p/ HTML.
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID ausentes.")
        return False

    # --- mensagem livre
    if isinstance(content, str):
        caption = _escape_html(content)
        return _send_html_text(caption)

    # --- sinal estruturado
    if not isinstance(content, dict):
        print("❌ Conteúdo de notificação não suportado.")
        return False

    # garante defaults mínimos
    signal = dict(content)
    signal.setdefault("action", "BUY")

    caption = _build_html_caption(signal)

    # tenta imagem
    png = None
    if _PIL_OK:
        try:
            png = _build_card_image(signal)
        except Exception as e:
            print(f"⚠️ Falha ao montar card: {e}")

    if png:
        ok = _send_photo_with_caption(png, caption)
        if ok:
            return True
        print("⚠️ Falha com imagem. Tentando texto…")
        return _send_html_text(caption)
    else:
        # sem PIL, vai de texto
        return _send_html_text(caption)


# =================================
# teste rápido local
# =================================
if __name__ == "__main__":
    demo = {
        "action": "BUY",
        "symbol": "BTCUSDT",
        "entry_price": 59850.12,
        "target_price": 61645.00,   # remova para testar fallback por TP_PCT
        "stop_loss": 58653.00,      # remova para testar fallback por SL_PCT
        "risk_reward": 2.0,
        "confidence_score": 0.72,   # aceita 0..1 ou 0..100
        "strategy": "RSI+MACD+EMA+BB+EXTRA",
        "created_at": "2025-08-21 12:34:56 UTC",
        "id": "sig-demo-123"
    }
    print("→ Enviando demo…")
    ok = send_signal_notification(demo)
    print("done:", ok)
