"""The senses saved for a word: how a lookup chose the first one, and the
sentence it was chosen for.

Standard library only; the terminal and the desktop app share it.
"""
from __future__ import annotations

from typing import Iterable

#: The longest sentence kept with a word ("context" in words.json). A reader
#: selection can span a paragraph, so only the text around the word is kept.
CONTEXT_LIMIT = 500

# What the EPUB reader strips from the ends of a selected word.
_EDGE_PUNCTUATION = ".,;:!?\"'()[]“”‘’"


class LookedUp(list):
    """The senses a lookup returned, and how their first one was chosen.

    ``how`` is "sentence" (ranked for the sentence), "first" (no sentence, or
    nothing to rank), "base" (DISAMBIG base keeps the dictionary's order) or
    "failed" (ranking raised, so the dictionary's order is kept; ``problem``
    says why). ``untagged`` is true when the ranking had no part-of-speech
    tagger.
    """

    def __init__(self, senses: Iterable = (), how: str = "first",
                 untagged: bool = False, problem: str = ""):
        super().__init__(senses)
        self.how, self.untagged, self.problem = how, untagged, problem

    def trimmed(self, limit: int) -> "LookedUp":
        return LookedUp(self[:limit], self.how, self.untagged, self.problem)


def _bare(text: str) -> str:
    return " ".join(text.split()).strip(_EDGE_PUNCTUATION).casefold()


def saved_context(word: str, sentence: str) -> str:
    """The sentence to keep with a word looked up from it, or "".

    A sentence that is only the word (the reader sends the selection itself
    when it cannot find the text around it) is not kept. One longer than
    CONTEXT_LIMIT is cut, at spaces, to the text around the word.
    """
    text = " ".join((sentence or "").split())
    if not text or _bare(text) == _bare(word):
        return ""
    if len(text) <= CONTEXT_LIMIT:
        return text
    room = CONTEXT_LIMIT - 2  # an ellipsis at each end that was cut
    at = max(text.casefold().find(word.strip().casefold()), 0)
    start = max(0, min(at + len(word.strip()) // 2 - room // 2, len(text) - room))
    end = start + room
    piece = text[start:end]
    if start > 0 and " " in piece:
        piece = piece[piece.index(" ") + 1:]  # never start inside a word
    if end < len(text) and " " in piece:
        piece = piece[:piece.rindex(" ")]
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def quoted_sentence(sentence: str, limit: int = 80) -> str:
    """A sentence in double quotes for a one-line reply, cut to ``limit``."""
    text = " ".join(sentence.split())
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return f'"{text}"'
