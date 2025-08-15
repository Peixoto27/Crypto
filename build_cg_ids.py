# -*- coding: utf-8 -*-
"""
build_cg_ids.py — gera/atualiza cg_ids.json mapeando SYMBOLS -> id do CoinGecko.

Como usar (local ou no Railway shell):
  python build_cg_ids.py
"""

import os, json, time, requests
from typing import Dict, List

CG_IDS_FILE = os.getenv("CG_IDS_FILE", "cg_ids.json")
SYMBOLS_ENV = os.getenv("SYMBOLS", "")  # ex: "BTCUSDT,ETHUSDT,..."
SYMBOLS = [s.strip() for s in SYMBOLS_ENV.split(",") if s.strip()]

COINLIST_CACHE = os.getenv("CG_COINLIST_CACHE", "cg_coinlist_cache.json")
API_BASE = "https://api.coingecko.com/api/v3"
TIMEOUT = 30

def _load_coinlist() -> List[dict]:
    # cache local para poupar chamadas
    if os.path.exists(COINLIST_CACHE):
        try:
            with open(COINLIST_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    print("🌐 Baixando coins/list…")
    r = requests.get(f"{API_BASE}/coins/list", timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json() or []
    try:
        with open(COINLIST_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return data

def _load_existing_map() -> Dict[str, str]:
    if os.path.exists(CG_IDS_FILE):
        try:
            with open(CG_IDS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                return {k.upper(): v for k, v in raw.items()}
        except Exception:
            pass
    return {}

def _save_map(m: Dict[str, str]):
    with open(CG_IDS_FILE, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    print(f"💾 Salvo {CG_IDS_FILE} ({len(m)} pares)")

def _resolve_id(symbol: str, coinlist: List[dict]) -> str:
    base = symbol.upper().replace("USDT", "").replace("USD", "").lower()
    # 1) match exato por "symbol"
    for c in coinlist:
        if (c.get("symbol") or "").lower() == base:
            return c.get("id")
    # 2) fallback: id contém base (cuidado com falsos-positivos; funciona bem para majors)
    for c in coinlist:
        cid = (c.get("id") or "").lower()
        if base and base in cid:
            return c.get("id")
    return ""

def main():
    if not SYMBOLS:
        print("⚠️ Nenhum SYMBOLS no env. Defina SYMBOLS=BTCUSDT,ETHUSDT,…")
        return
    coinlist = _load_coinlist()
    m = _load_existing_map()
    total = 0
    for sym in SYMBOLS:
        total += 1
        if sym.upper() in m and m[sym.upper()]:
            continue
        cid = _resolve_id(sym, coinlist)
        if cid:
            m[sym.upper()] = cid
            print(f"✅ {sym} -> {cid}")
        else:
            print(f"❌ Não encontrado: {sym}")
    _save_map(m)
    print(f"✔️ Concluído. Mapeados: {len(m)}/{total}")

if __name__ == "__main__":
    main()
a
