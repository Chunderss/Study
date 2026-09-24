"""Spaced-repetition schedulers.

The Scheduler interface is the seam that lets the repetition algorithm be
swapped (the spec explicitly wants this). Leitner ships first; SM-2 / FSRS can
be added later as new implementations without touching the study loop.
"""
from .base import Scheduler, Review, get_scheduler, SCHEDULERS
from .leitner import LeitnerScheduler

__all__ = ["Scheduler", "Review", "LeitnerScheduler", "get_scheduler", "SCHEDULERS"]
