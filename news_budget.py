# -*- coding: utf-8 -*-
# news_budget.py — controle de orçamento mensal e limite por ciclo para News

import os, json, time
from datetime import datetime

BUDGET_FILE = os.getenv("NEWS_BUDGET_FILE", "news_budget.json")

def _month_key(ts=None):
    dt = datetime.utcfromtimestamp(ts or time.time())
    return f"{dt.year:04d}-{dt.month:02d}"

class NewsBudget:
    """
    - NEWS_MONTHLY_BUDGET=100         # chamadas p/ mês
    - NEWS_CALLS_PER_CYCLE_MAX=1      # máx. por ciclo (ex.: a cada 20min)
    """
    def __init__(self):
        self.monthly_budget = int(os.getenv("NEWS_MONTHLY_BUDGET", "100"))
        self.calls_per_cycle_max = int(os.getenv("NEWS_CALLS_PER_CYCLE_MAX", "1"))
        self.state = self._load()

    def _load(self):
        try:
            with open(BUDGET_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"month": _month_key(), "used": 0, "cycle_used": 0, "cycle_ts": 0}

    def _save(self):
        with open(BUDGET_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def _reset_if_new_month(self):
        mk = _month_key()
        if self.state.get("month") != mk:
            self.state = {"month": mk, "used": 0, "cycle_used": 0, "cycle_ts": 0}

    def new_cycle(self):
        """Chamar 1x no início de cada ciclo do runner."""
        self._reset_if_new_month()
        self.state["cycle_used"] = 0
        self.state["cycle_ts"] = int(time.time())
        self._save()

    def allow_call(self) -> bool:
        """Pode chamar a API agora? (orçamento do mês e limite do ciclo)"""
        self._reset_if_new_month()
        if self.state["used"] >= self.monthly_budget:
            return False
        if self.state["cycle_used"] >= self.calls_per_cycle_max:
            return False
        return True

    def consume(self):
        """Registra 1 chamada gasta."""
        self._reset_if_new_month()
        self.state["used"] += 1
        self.state["cycle_used"] += 1
        self._save()

    def remaining_month(self) -> int:
        self._reset_if_new_month()
        return max(0, self.monthly_budget - self.state["used"])
