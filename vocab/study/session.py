"""Building a study queue from one or many lists.

Key design point (per the STUDYALL ruling): a StudyCard remembers its HOME list
so that when it's reviewed inside a STUDYALL session, the updated scheduler
state is written back to the word's home list — never a divergent copy. STUDYALL
is therefore an ephemeral virtual session, not a persisted mixed list.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, NoReturn

from ..algorithms.base import Review, Scheduler
from ..core.models import Word
from ..core.storage import ModuleNotFound, Storage


class StudyCardUnavailable(ValueError):
    """The displayed card was skipped without a review: it changed outside this
    session."""


@dataclass
class StudyCard:
    home_list: str          # where progress is written back
    word: Word
    card: dict              # scheduler state


@dataclass
class SessionResult:
    reviewed: int = 0
    correct: int = 0
    skipped: int = 0        # changed or deleted elsewhere
    overruled: int = 0      # mechanical mode: recorded against the judge's suggestion
    ungraded: int = 0       # the judge could not grade the typed answer (desktop)

    def mark(self, correct: bool) -> None:
        self.reviewed += 1
        self.correct += int(correct)


def _due_cards_for_list(storage: Storage, scheduler: Scheduler, name: str,
                        now_ts: float) -> List[StudyCard]:
    def read():
        wl = storage.load_words(name)
        stats = storage.load_stats(name)
        return wl, stats, [key for key in wl.words if stats.get(key) is None]

    wl, stats, missing = read()
    if missing:
        # Backfill new cards so they persist; re-read under the write lock.
        with storage.transaction():
            wl, stats, missing = read()
            for key in missing:
                stats[key] = scheduler.new_card()
            if missing:
                storage.save_stats(name, stats)
    cards: List[StudyCard] = [StudyCard(home_list=name, word=word, card=stats[key])
                              for key, word in wl.words.items()]
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
class Judgement:
    """The judge's suggestion for a typed answer. Nothing is recorded until the
    user confirms or overrules it with StudySession.answer()."""
    typed: str
    reference: str               # the definition the answer was compared with
    feedback: str
    score: float | None          # 0..1, None when the judge cannot grade the card
    suggested: bool | None       # the judge's verdict, None when it cannot grade


@dataclass
class AnswerOutcome:
    """Result of grading one card, for the frontend to show feedback."""
    correct: bool
    box_before: int
    box_after: int
    score: float | None = None   # judge score in mechanical mode (0..1), else None
    feedback: str = ""           # judge feedback text in mechanical mode
    reference: str = ""          # the reference definition
    suggested: bool | None = None  # mechanical mode: the judge's verdict, if any

    @property
    def overruled(self) -> bool:
        return self.suggested is not None and self.suggested != self.correct


class StudySession:
    """Non-blocking, UI-agnostic study state machine.

    Both the terminal REPL and the Qt GUI drive this the same way::

        s = StudySession(storage, scheduler, names, mechanical, judge)
        while not s.done:
            view = s.current()          # what to show
            # ... show the word, let the user recall ...
            # mechanical mode first: j = s.judge_text("their typed def"), then
            # show j.reference, j.feedback and j.suggested
            outcome = s.answer(True)    # the user's verdict; recorded now
        summary = s.finish()            # returns the summary text

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
        self._judgement: Judgement | None = None   # for the current card

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
    @property
    def judgement(self) -> Judgement | None:
        """The judge's suggestion for the current card, until it is answered."""
        return self._judgement

    def _advance(self) -> None:
        self._i += 1
        self._judgement = None

    def _unavailable(self, reason: str, *, ungraded: bool = False) -> NoReturn:
        word = self.queue[self._i].word.word
        self._advance()
        if ungraded:
            self.result.ungraded += 1
        else:
            self.result.skipped += 1
        raise StudyCardUnavailable(f"Skipped '{word}': {reason}")

    def _apply(self, correct: bool, judgement: Judgement | None = None) -> AnswerOutcome:
        sc = self.queue[self._i]
        key = sc.word.word.strip().lower()
        # Check and record under the write lock, so another process cannot
        # change this card between the check and the save.
        with self.storage.transaction():
            try:
                wl = self.storage.load_words(sc.home_list)
            except ModuleNotFound:
                self._unavailable("the module was deleted.")
            if not wl.has(key):
                self._unavailable("the word was deleted.")
            stats = self.storage.load_stats(sc.home_list)
            current_word = wl.words[key]
            # Legacy words without an added timestamp get a default on each load;
            # compare the answer content rather than that generated metadata.
            if (stats.get(key) != sc.card or current_word.senses != sc.word.senses or
                    current_word.dictionary != sc.word.dictionary):
                self._unavailable("the word or its progress changed in another session.")
            box_before = int(sc.card.get("box", 1))
            new_card = self.scheduler.review(
                sc.card, Review.GOOD if correct else Review.AGAIN, time.time())
            stats[key] = new_card
            self.storage.save_stats(sc.home_list, stats)
        outcome = AnswerOutcome(
            correct=correct, box_before=box_before,
            box_after=int(new_card.get("box", 1)),
            reference=sc.word.primary_definition())
        if judgement is not None:
            outcome.score, outcome.feedback = judgement.score, judgement.feedback
            outcome.reference, outcome.suggested = judgement.reference, judgement.suggested
        self.result.mark(correct)
        self.result.overruled += int(outcome.overruled)
        self._advance()
        return outcome

    def judge_text(self, typed: str) -> Judgement:
        """Mechanical mode: the judge's suggestion for a typed definition.
        Records nothing; answer() records the verdict the user settles on."""
        if self.done:
            raise RuntimeError("Session already finished.")
        if self.judge is None:
            raise RuntimeError("No judge configured for mechanical mode.")
        typed = typed.strip()
        if not typed:
            raise ValueError("Type a definition first.")
        reference = self.queue[self._i].word.primary_definition()
        jr = self.judge.score(typed, reference)
        self._judgement = Judgement(
            typed=typed, reference=reference, feedback=jr.feedback,
            score=jr.score if jr.gradable else None,
            suggested=jr.passed if jr.gradable else None)
        return self._judgement

    def answer(self, correct: bool) -> AnswerOutcome:
        """Record the user's verdict on the current card. In mechanical mode the
        typed answer must have been judged first; the verdict may differ."""
        if self.done:
            raise RuntimeError("Session already finished.")
        if self.mechanical and self._judgement is None:
            raise RuntimeError("Type a definition first.")
        return self._apply(bool(correct), self._judgement)

    def answer_text(self, typed: str) -> AnswerOutcome:
        """The study view's path until it asks for a verdict: record the
        judge's verdict at once, and skip a card the judge cannot grade."""
        judgement = self.judge_text(typed)
        if judgement.suggested is None:
            self._unavailable("its definition has no words to compare a typed answer "
                              "with. Study it with MODE MECH OFF.", ungraded=True)
        return self.answer(judgement.suggested)

    def skip(self) -> None:
        """Advance without recording a review (used when the user quits early)."""
        self._advance()

    def finish(self) -> str:
        """Return the session summary.

        Reviews were committed when answered. Replaying old cards here could
        overwrite progress from another session or resurrect deleted words.
        """
        acc = (self.result.correct / self.result.reviewed * 100) if self.result.reviewed else 0.0
        summary = f"Session done: {self.result.correct}/{self.result.reviewed} correct ({acc:.0f}%)."
        if self.result.overruled:
            summary += f" You overruled the judge on {self.result.overruled} card(s)."
        if self.result.skipped:
            summary += f" Skipped {self.result.skipped} changed or deleted card(s)."
        if self.result.ungraded:
            summary += (f" Skipped {self.result.ungraded} card(s) whose definition "
                        "cannot be graded by typing.")
        return summary
