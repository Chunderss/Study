"""Building a study queue from one or many lists.

Key design point (per the STUDYALL ruling): a StudyCard remembers its HOME list
so that when it's reviewed inside a STUDYALL session, the updated scheduler
state is written back to the word's home list — never a divergent copy. STUDYALL
is therefore an ephemeral virtual session, not a persisted mixed list.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List

from ..algorithms.base import Review, Scheduler
from ..core.models import Word
from ..core.storage import Storage


@dataclass
class StudyCard:
    home_list: str          # where progress is written back
    word: Word
    card: dict              # scheduler state


@dataclass
class SessionResult:
    reviewed: int = 0
    correct: int = 0
    # home_list -> {word_key -> updated card}; flushed by the caller
    dirty: Dict[str, Dict[str, dict]] = field(default_factory=dict)

    def mark(self, home_list: str, key: str, card: dict, correct: bool) -> None:
        self.reviewed += 1
        self.correct += int(correct)
        self.dirty.setdefault(home_list, {})[key] = card


def _due_cards_for_list(storage: Storage, scheduler: Scheduler, name: str,
                        now_ts: float) -> List[StudyCard]:
    wl = storage.load_words(name)
    stats = storage.load_stats(name)
    changed = False
    cards: List[StudyCard] = []
    for key, word in wl.words.items():
        card = stats.get(key)
        if card is None:
            card = scheduler.new_card()
            stats[key] = card
            changed = True
        cards.append(StudyCard(home_list=name, word=word, card=card))
    if changed:
        storage.save_stats(name, stats)  # backfill new cards so they persist
    # order by scheduler due logic
    by_key = {sc.word.word.strip().lower(): sc for sc in cards}
    ordered_keys = scheduler.due_order({k: sc.card for k, sc in by_key.items()}, now_ts)
    return [by_key[k] for k in ordered_keys]


def build_session(storage: Storage, scheduler: Scheduler, names: List[str],
                  now_ts: float | None = None) -> List[StudyCard]:
    """Build a due-card queue across one or more lists (STUDYALL passes many).

    Cards are interleaved by scheduler priority so a multi-list session doesn't
    front-load one list. Each card retains its home_list for write-back.
    """
    now_ts = time.time() if now_ts is None else now_ts
    per_list = [_due_cards_for_list(storage, scheduler, n, now_ts) for n in names]
    # Round-robin interleave to mix lists fairly.
    queue: List[StudyCard] = []
    idx = 0
    while any(idx < len(pl) for pl in per_list):
        for pl in per_list:
            if idx < len(pl):
                queue.append(pl[idx])
        idx += 1
    return queue


@dataclass
class CardView:
    """Everything a frontend needs to render the current card."""
    index: int              # 1-based position in the session
    total: int
    word: str
    home_list: str
    pos: str                # part of speech of the primary sense
    definition: str         # primary definition (revealed answer)
    example: str            # example sentence for the primary sense, if any
    box_before: int         # Leitner box before this review (for animation)


@dataclass
class AnswerOutcome:
    """Result of grading one card, for the frontend to show feedback."""
    correct: bool
    box_before: int
    box_after: int
    score: float | None = None   # judge score in mechanical mode (0..1), else None
    feedback: str = ""           # judge feedback text in mechanical mode
    reference: str = ""          # the reference definition


class StudySession:
    """Non-blocking, UI-agnostic study state machine.

    Both the terminal REPL and the Qt GUI drive this the same way::

        s = StudySession(storage, scheduler, names, mechanical, judge)
        while not s.done:
            view = s.current()          # what to show
            # ... show the word, let the user recall ...
            outcome = s.answer(True)    # reveal mode: correct/incorrect
            # or in mechanical mode:  outcome = s.answer_text("their typed def")
        summary = s.finish()            # flushes progress, returns summary text

    It never calls input()/print(); the caller owns all I/O and timing. This is
    what lets a GUI event loop drive it without a blocking prompt loop.
    """

    def __init__(self, storage: Storage, scheduler: Scheduler, names: List[str],
                 mechanical: bool = False, judge=None,
                 session_label: str | None = None):
        self.storage = storage
        self.scheduler = scheduler
        self.mechanical = mechanical
        self.judge = judge
        self.label = session_label or ", ".join(names)
        self.queue: List[StudyCard] = build_session(storage, scheduler, names)
        self.result = SessionResult()
        self._i = 0

    # ---- introspection --------------------------------------------------
    @property
    def total(self) -> int:
        return len(self.queue)

    @property
    def done(self) -> bool:
        return self._i >= len(self.queue)

    @property
    def reviewed(self) -> int:
        return self.result.reviewed

    @property
    def correct(self) -> int:
        return self.result.correct

    def current(self) -> CardView | None:
        if self.done:
            return None
        sc = self.queue[self._i]
        sense = sc.word.senses[0] if sc.word.senses else None
        return CardView(
            index=self._i + 1,
            total=len(self.queue),
            word=sc.word.word,
            home_list=sc.home_list,
            pos=(sense.pos if sense else ""),
            definition=sc.word.primary_definition(),
            example=(sense.example if sense else ""),
            box_before=int(sc.card.get("box", 1)),
        )

    # ---- actions --------------------------------------------------------
    def _apply(self, correct: bool, *, score=None, feedback="", reference="") -> AnswerOutcome:
        sc = self.queue[self._i]
        box_before = int(sc.card.get("box", 1))
        new_card = self.scheduler.review(
            sc.card, Review.GOOD if correct else Review.AGAIN, time.time())
        key = sc.word.word.strip().lower()
        wl = self.storage.load_words(sc.home_list)
        if not wl.has(key):
            raise ValueError(f"'{sc.word.word}' was deleted during this session.")
        stats = self.storage.load_stats(sc.home_list)
        stats[key] = new_card
        self.storage.save_stats(sc.home_list, stats)
        self.result.mark(sc.home_list, key, new_card, correct)
        self._i += 1
        return AnswerOutcome(
            correct=correct, box_before=box_before,
            box_after=int(new_card.get("box", 1)),
            score=score, feedback=feedback,
            reference=reference or sc.word.primary_definition(),
        )

    def answer(self, correct: bool) -> AnswerOutcome:
        """Reveal-mode grading: caller says whether the user got it."""
        if self.done:
            raise RuntimeError("Session already finished.")
        return self._apply(bool(correct))

    def answer_text(self, typed: str) -> AnswerOutcome:
        """Mechanical-mode grading: judge the typed definition."""
        if self.done:
            raise RuntimeError("Session already finished.")
        if self.judge is None:
            raise RuntimeError("No judge configured for mechanical mode.")
        reference = self.queue[self._i].word.primary_definition()
        jr = self.judge.score(typed, reference)
        return self._apply(jr.score >= 0.5, score=jr.score,
                           feedback=jr.feedback, reference=reference)

    def skip(self) -> None:
        """Advance without recording a review (used when the user quits early)."""
        self._i += 1

    def finish(self) -> str:
        """Flush all reviewed cards back to their HOME lists; return a summary."""
        # Reviews were committed when answered. Replaying old cards here could
        # overwrite progress from another session or resurrect deleted words.
        self.result.dirty.clear()
        acc = (self.result.correct / self.result.reviewed * 100) if self.result.reviewed else 0.0
        return f"Session done: {self.result.correct}/{self.result.reviewed} correct ({acc:.0f}%)."
