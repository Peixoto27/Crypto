# -*- coding: utf-8 -*-
import os, json
from datetime import datetime, timedelta
from typing import Dict, Any

POSITIONS_FILE = os.getenv("POSITIONS_FILE", "positions.json")

def _ensure_file(path: str, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)

_ensure_file(POSITIONS_FILE, {"open": [], "closed": []})

def _now_utc() -> datetime:
    return datetime.utcnow()

def _load_book() -> Dict[str, Any]:
    try:
        with open(POSITIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"open": [], "closed": []}

def _save_book(book: Dict[str, Any]) -> None:
    with open(POSITIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=2)

def _pct_diff(a, b) -> float:
    try:
        a = float(a); b = float(b)
        if a == 0: return 999.0
        return abs(a - b) / abs(a) * 100.0
    except Exception:
        return 999.0

def should_send_and_register(sig: Dict[str, Any], cooldown_hours: float = 6.0, change_threshold_pct: float = 1.0):
    """
    Regras:
      - Se não houver posição aberta do símbolo -> registra e envia.
      - Se houver:
          * se mudou entry/tp/sl acima do limiar -> atualiza e envia.
          * senão, se passou o cooldown -> atualiza timestamp e envia.
          * caso contrário -> não envia (duplicado).
    Retorna: (ok_to_send: bool, reason: str)
    """
    symbol = sig.get("symbol")
    if not symbol:
        return False, "sem_symbol"

    entry = sig.get("entry"); tp = sig.get("tp"); sl = sig.get("sl")
    book = _load_book()
    open_list = book.get("open", [])
    now = _now_utc()

    found = None
    for pos in open_list:
        if pos.get("symbol") == symbol and pos.get("status", "open") == "open":
            found = pos
            break

    if found is None:
        open_list.append({
            "symbol": symbol,
            "entry": float(entry) if entry is not None else None,
            "tp": float(tp) if tp is not None else None,
            "sl": float(sl) if sl is not None else None,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "last_sent_at": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status": "open"
        })
        book["open"] = open_list
        _save_book(book)
        return True, "novo"

    changed = (
        _pct_diff(found.get("entry"), entry) > change_threshold_pct or
        _pct_diff(found.get("tp"), tp) > change_threshold_pct or
        _pct_diff(found.get("sl"), sl) > change_threshold_pct
    )

    cooldown_ok = True
    last_sent = found.get("last_sent_at")
    if last_sent:
        try:
            last_dt = datetime.strptime(last_sent, "%Y-%m-%d %H:%M:%S UTC")
            cooldown_ok = (now - last_dt) >= timedelta(hours=float(cooldown_hours))
        except Exception:
            cooldown_ok = True

    if changed or cooldown_ok:
        if entry is not None: found["entry"] = float(entry)
        if tp is not None:    found["tp"]    = float(tp)
        if sl is not None:    found["sl"]    = float(sl)
        found["last_sent_at"] = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        _save_book(book)
        return True, ("mudou" if changed else "cooldown")

    return False, "duplicado"

def close_position(symbol: str, reason: str) -> bool:
    """
    Fecha a posição do símbolo (move open -> closed) com reason: 'hit_tp'|'hit_sl'|'expirado'
    """
    book = _load_book()
    new_open = []
    closed = book.get("closed", [])
    now = _now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")

    moved = False
    for pos in book.get("open", []):
        if pos.get("symbol") == symbol and pos.get("status", "open") == "open":
            pos["status"] = reason
            pos["closed_at"] = now
            closed.append(pos)
            moved = True
        else:
            new_open.append(pos)

    book["open"] = new_open
    book["closed"] = closed
    _save_book(book)
    return moved
