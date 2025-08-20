# signal_generator.py
# -*- coding: utf-8 -*-

"""
Utilitários para construção e persistência de sinais.
Inclui:
- append_signal(lista_existente, novo_sinal)  -> retorna a lista atualizada
- save_signal(symbol=..., score=..., reason=..., extra=..., path='signals.json')
- load_signals(path), write_signals(signals, path)

Compatível com usos antigos do main.py que fazem:
    from signal_generator import append_signal
"""

from __future__ import annotations
import json
import os
import io
import time
import threading
from typing import Any, Dict, List, Optional, Union

# Lock global simples p/ evitar escrita concorrente
_WRITE_LOCK = threading.Lock()

def _now_ts() -> int:
    return int(time.time())

def _safe_read_json(path: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Lê JSON com tolerância (arquivo inexistente, vazio ou inválido)."""
    if not os.path.exists(path):
        return []
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
            if not text:
                return []
            return json.loads(text)
    except Exception:
        # Se estiver corrompido, não quebra o pipeline.
        return []

def _safe_write_json(obj: Union[List, Dict], path: str) -> None:
    """Escreve JSON de forma atômica (tmp + rename) com UTF-8."""
    tmp = f"{path}.tmp"
    content = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), indent=2)
    with _WRITE_LOCK:
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)

def load_signals(path: str = "signals.json") -> List[Dict[str, Any]]:
    data = _safe_read_json(path)
    if isinstance(data, dict):
        # caso versões antigas salvem dict
        data = data.get("signals", [])
        if not isinstance(data, list):
            data = []
    return data  # lista

def write_signals(signals: List[Dict[str, Any]], path: str = "signals.json") -> None:
    _safe_write_json(signals, path)

def _normalize_signal(sig: Dict[str, Any]) -> Dict[str, Any]:
    """Garante campos padrão e tipos simples."""
    out = dict(sig) if isinstance(sig, dict) else {}
    out.setdefault("ts", _now_ts())
    # normaliza score para float se possível
    try:
        if "score" in out and not isinstance(out["score"], (int, float)):
            out["score"] = float(out["score"])
    except Exception:
        pass
    # garante string em symbol/reason
    if "symbol" in out and out["symbol"] is not None:
        out["symbol"] = str(out["symbol"])
    if "reason" in out and out["reason"] is not None:
        out["reason"] = str(out["reason"])
    return out

def append_signal(signals_or_path: Union[List[Dict[str, Any]], str],
                  new_signal: Optional[Dict[str, Any]] = None,
                  *,
                  symbol: Optional[str] = None,
                  score: Optional[Union[int, float, str]] = None,
                  reason: Optional[str] = None,
                  extra: Optional[Dict[str, Any]] = None,
                  path: str = "signals.json") -> List[Dict[str, Any]]:
    """
    Modo A (compatível com main.py mais comum):
        append_signal(signals_list, {"symbol":"BTCUSDT","score":88.0,"reason":"setup X"})
        -> retorna a lista atualizada

    Modo B (persistente direto no arquivo):
        append_signal("signals.json", symbol="BTCUSDT", score=88.0, reason="setup Y", extra={...})
        -> retorna a lista atualizada lida + escrita no arquivo

    Parâmetros nomeados (symbol/score/reason/extra) só são usados no Modo B.
    """
    # Detecta modo
    if isinstance(signals_or_path, list):
        # Modo A: só mexe na lista fornecida
        signals = signals_or_path
        sig = _normalize_signal(new_signal or {})
        signals.append(sig)
        return signals

    # Modo B: path de arquivo
    file_path = signals_or_path or path
    signals = load_signals(file_path)
    sig = {
        "symbol": symbol,
        "score": score,
        "reason": reason,
        "extra": extra or {},
        "ts": _now_ts(),
    }
    sig = _normalize_signal(sig)
    signals.append(sig)
    write_signals(signals, file_path)
    return signals

def save_signal(symbol: str,
                score: Union[int, float, str],
                reason: str = "",
                *,
                extra: Optional[Dict[str, Any]] = None,
                path: str = "signals.json") -> None:
    """
    Atalho prático: salva direto no arquivo.
    Ex.:
        save_signal("BTCUSDT", 91.2, "Mix>=70 e filtro ok")
    """
    append_signal(path, symbol=symbol, score=score, reason=reason, extra=extra, path=path)

# Opcional: utilitário para limpar sinais antigos (ex.: > 7 dias)
def prune_old_signals(days: int = 7, path: str = "signals.json") -> int:
    cutoff = _now_ts() - days * 86400
    signals = load_signals(path)
    new_list = [s for s in signals if int(s.get("ts", 0)) >= cutoff]
    if len(new_list) != len(signals):
        write_signals(new_list, path)
    return len(signals) - len(new_list)
