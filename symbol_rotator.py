# -*- coding: utf-8 -*-
import json, os
from typing import List
from symbols_pool import ALWAYS_SYMBOLS, ROTATING_SYMBOLS

STATE_FILE        = os.getenv("ROTATOR_STATE_FILE", "rotor_state.json")
PRIORITY_FILE     = os.getenv("ROTATOR_PRIORITY_FILE", "rotor_priority.json")
SELECT_PER_CYCLE  = int(os.getenv("SELECT_PER_CYCLE", "12"))  # total por ciclo (inclui os FIXOS)

def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)

def _take_priority(max_k: int) -> list:
    q = _load(PRIORITY_FILE, {"queue":[]})
    take = q["queue"][:max_k]
    if take:
        q["queue"] = q["queue"][len(take):]
        _save(PRIORITY_FILE, q)
    return take

def push_priority(symbols: List[str]):
    """Opcional: chame isso quando sentimento/score explodir para furar a fila no próximo ciclo."""
    q = _load(PRIORITY_FILE, {"queue":[]})
    # evita duplicados preservando ordem
    seen = set(q["queue"])
    for s in symbols:
        if s not in seen:
            q["queue"].append(s); seen.add(s)
    _save(PRIORITY_FILE, q)

def get_next_batch() -> List[str]:
    # 1) começa pelos FIXOS
    batch = list(ALWAYS_SYMBOLS)

    # 2) puxa prioridade (notícias/near-miss, etc.)
    space = max(0, SELECT_PER_CYCLE - len(batch))
    if space > 0:
        batch += _take_priority(space)
        space = max(0, SELECT_PER_CYCLE - len(batch))

    # 3) completa com rotação circular
    st = _load(STATE_FILE, {"idx": 0})
    idx = int(st.get("idx", 0)) % max(1, len(ROTATING_SYMBOLS))
    k = max(0, min(space, len(ROTATING_SYMBOLS)))

    part1 = ROTATING_SYMBOLS[idx: idx + k]
    if len(part1) < k:
        part2 = ROTATING_SYMBOLS[: k - len(part1)]
        pick = part1 + part2
        st["idx"] = (k - len(part1))
    else:
        pick = part1
        st["idx"] = idx + k

    _save(STATE_FILE, st)

    # dedupe (caso algum FIXO também esteja nos rotativos ou prioridade)
    final, seen = [], set()
    for s in batch + pick:
        if s not in seen:
            seen.add(s); final.append(s)

    return final
