"""Leitner box system (the same idea Anki's ancestors used).

A card lives in a box 1..N. A correct answer promotes it one box (longer
interval); a wrong answer sends it back to box 1. Intervals are per-box, in
calendar days. All timestamps are POSIX seconds (UTC) so stats.json is portable.

Calendar days (see local_day_start): an answer on a given local day makes the
card due at the start of the local day 1, 3, 7, 16 or 35 days later, whatever
the time of the answer. A day starts at local midnight, or at its first time
when the clocks jump past midnight, so it lasts 23 or 25 hours across a
daylight-saving change. "due" stores the instant the due day starts where the
answer was given.

A card is due from the start of the local day that contains its "due" time.
That one rule covers three cases: new values; progress saved by older versions
(the answer time plus whole 24-hour periods), which comes due at the start of
its day, up to 24 hours earlier and never later, without being rewritten; and a
change of time zone or of daylight-saving rules, after which a card keeps its
date when the clocks move forward (east) and can come due a day early when they
move back (west), never later than the stored instant. Local time follows the
zone the process read from the computer (TZ or the system setting); Python
reads it at start-up, so changing the computer's zone may take effect only
when the app restarts.

A day start other than midnight (a rollover hour such as 04:00) would need its
own compatibility rule: every stored midnight instant lies in the study day
before it, so the rule above would make those cards due almost a day early.

State shape (stored in stats.json under each word key)::

    {"algo": "leitner", "box": 2, "due": 1725500000.0,
     "reps": 5, "lapses": 1, "last": 1725400000.0}
"""
from __future__ import annotations

import math
from datetime import datetime, time as clock_time, timedelta
from typing import Dict, List

from .base import Review, Scheduler, register

_DAY = 86400.0
# Box -> interval in calendar days from the day of the answer. Boxes start at
# 1, so index 0 is unused. A new card is due at once; a missed card goes back
# to box 1 and is due again from the start of the next day, never straight away.
_INTERVALS = [0, 1, 3, 7, 16, 35]  # index by box; box>=5 caps at 35d
_MAX_BOX = len(_INTERVALS) - 1


def local_day_start(ts: float, days: int = 0) -> float:
    """The first instant of the local calendar day ``days`` days after the day
    that contains ``ts``, in POSIX seconds, by this computer's time zone rules.

    A day lasts 23 or 25 hours across a daylight-saving change. Where a day
    has no midnight (clocks jump forward at 00:00) it starts at its first
    existing time; a day the zone skipped entirely gives the next day's start.
    """
    try:
        day = datetime.fromtimestamp(ts).date() + timedelta(days=days)
        # fold=0: a midnight inside a forward jump maps past the jump.
        return datetime.combine(day, clock_time()).timestamp()
    except (OverflowError, OSError, ValueError):
        # Outside what the platform can convert (e.g. before 1970 on
        # Windows): count UTC days instead.
        return (math.floor(ts / _DAY) + days) * _DAY


class LeitnerScheduler(Scheduler):
    id = "leitner"
    name = "Leitner box system"

    def card_problem(self, card):
        """Reject values the scheduler could not use (a due date of "tomorrow",
        NaN, a fractional box). Missing fields get the usual defaults."""
        if not isinstance(card, dict):
            return "progress is not an object"
        if card.get("algo", self.id) != self.id:
            return f"progress is for the '{card.get('algo')}' scheduler"
        for field, lowest in (("box", 1), ("reps", 0), ("lapses", 0)):
            value = card.get(field, lowest)
            if isinstance(value, bool) or not isinstance(value, int) or value < lowest:
                return f"'{field}' must be a whole number of at least {lowest}"
        for field in ("due", "last"):
            value = card.get(field, 0.0)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value)):
                return f"'{field}' must be a time in seconds"
        return None

    def new_card(self) -> dict:
        return {"algo": self.id, "box": 1, "due": 0.0,
                "reps": 0, "lapses": 0, "last": 0.0}

    def _interval_days(self, box: int) -> int:
        return _INTERVALS[max(1, min(box, _MAX_BOX))]

    def is_due(self, card: dict, now_ts: float) -> bool:
        return float(card.get("due", 0.0)) < local_day_start(now_ts, 1)

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
        c["due"] = local_day_start(now_ts, self._interval_days(int(c["box"])))
        return c

    def due_order(self, cards: Dict[str, dict], now_ts: float) -> List[str]:
        # Due today or earlier: before the start of tomorrow.
        tomorrow = local_day_start(now_ts, 1)
        due = [(k, v) for k, v in cards.items() if float(v.get("due", 0.0)) < tomorrow]
        # Lowest box first (weakest words), then oldest due date.
        due.sort(key=lambda kv: (int(kv[1].get("box", 1)), float(kv[1].get("due", 0.0))))
        return [k for k, _ in due]

    def summary(self, card: dict) -> str:
        box = int(card.get("box", 1))
        return f"box {box}/{_MAX_BOX}, reps {card.get('reps', 0)}, lapses {card.get('lapses', 0)}"


register(LeitnerScheduler())
