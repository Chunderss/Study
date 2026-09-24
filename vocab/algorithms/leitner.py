"""Leitner box system (the same idea Anki's ancestors used).

A card lives in a box 1..N. A correct answer promotes it one box (longer
interval); a wrong answer sends it back to box 1. Intervals are per-box, in
days. All timestamps are POSIX seconds (UTC) so stats.json is portable.

State shape (stored in stats.json under each word key)::

    {"algo": "leitner", "box": 2, "due": 1725500000.0,
     "reps": 5, "lapses": 1, "last": 1725400000.0}
"""
from __future__ import annotations

from typing import Dict, List

from .base import Review, Scheduler, register

_DAY = 86400.0
# Box -> interval in days. Box 1 is due immediately (same session churn).
_INTERVALS = [0, 1, 3, 7, 16, 35]  # index by box; box>=5 caps at 35d
_MAX_BOX = len(_INTERVALS) - 1


class LeitnerScheduler(Scheduler):
    id = "leitner"
    name = "Leitner box system"

    def new_card(self) -> dict:
        return {"algo": self.id, "box": 1, "due": 0.0,
                "reps": 0, "lapses": 0, "last": 0.0}

    def _interval(self, box: int) -> float:
        box = max(1, min(box, _MAX_BOX))
        return _INTERVALS[box] * _DAY

    def is_due(self, card: dict, now_ts: float) -> bool:
        return float(card.get("due", 0.0)) <= now_ts

    def review(self, card: dict, outcome: Review, now_ts: float) -> dict:
        c = dict(card)
        c.setdefault("box", 1)
        c["reps"] = int(c.get("reps", 0)) + 1
        c["last"] = now_ts
        if outcome == Review.GOOD:
            c["box"] = min(int(c["box"]) + 1, _MAX_BOX)
        else:
            c["box"] = 1
            c["lapses"] = int(c.get("lapses", 0)) + 1
        c["due"] = now_ts + self._interval(int(c["box"]))
        return c

    def due_order(self, cards: Dict[str, dict], now_ts: float) -> List[str]:
        due = [(k, v) for k, v in cards.items() if self.is_due(v, now_ts)]
        # Lowest box first (weakest words), then oldest due date.
        due.sort(key=lambda kv: (int(kv[1].get("box", 1)), float(kv[1].get("due", 0.0))))
        return [k for k, _ in due]

    def summary(self, card: dict) -> str:
        box = int(card.get("box", 1))
        return f"box {box}/{_MAX_BOX}, reps {card.get('reps', 0)}, lapses {card.get('lapses', 0)}"


register(LeitnerScheduler())
