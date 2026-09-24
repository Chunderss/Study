"""Scheduler interface + shared review outcome type."""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, List, Tuple


class Review(str, Enum):
    AGAIN = "again"   # wrong / forgot -> demote
    GOOD = "good"     # correct -> promote


class Scheduler(ABC):
    id: str = ""
    name: str = ""

    @abstractmethod
    def new_card(self) -> dict:
        """Initial per-word state for a freshly added word."""

    @abstractmethod
    def is_due(self, card: dict, now_ts: float) -> bool:
        ...

    @abstractmethod
    def review(self, card: dict, outcome: Review, now_ts: float) -> dict:
        """Return the updated card after a review outcome."""

    @abstractmethod
    def due_order(self, cards: Dict[str, dict], now_ts: float) -> List[str]:
        """Return keys of due cards, in the order they should be studied."""

    def summary(self, card: dict) -> str:
        """Short human string for STATS, e.g. 'box 2, due in 1d'."""
        return ""


# populated at import time by algorithms.__init__ via register()
SCHEDULERS: Dict[str, Scheduler] = {}


def register(scheduler: Scheduler) -> Scheduler:
    SCHEDULERS[scheduler.id] = scheduler
    return scheduler


def get_scheduler(sched_id: str) -> Scheduler:
    try:
        return SCHEDULERS[sched_id]
    except KeyError:
        raise KeyError(f"Unknown algorithm '{sched_id}'. Known: {', '.join(SCHEDULERS)}")
