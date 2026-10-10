"""WordNet backend (offline) via NLTK.

Optional feature. `available()` is False until both the `nltk` package and the
`wordnet` corpus are present. `install()` downloads nltk_resources.NLTK_PACKAGES
on demand: the corpus and the part-of-speech tagger that sense selection uses.
The CLI surfaces that cost before calling it, and the desktop build bundles the
same packages (tools/build_desktop.py, VocabStudy.spec).
"""
from __future__ import annotations

from typing import List

from .. import nltk_resources
from ..core.models import Sense
from .base import Dictionary, LookupFailed

_POS_MAP = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}


class WordNet(Dictionary):
    id = "wordnet"
    name = "WordNet (offline)"
    description = "Offline lexical database. Works with no network once installed."
    install_cost = "~13 MB (English WordNet corpus and part-of-speech tagger)"
    requires_network = False  # at lookup time; install needs network once

    def _corpus(self):
        try:
            from nltk.corpus import wordnet as wn
            wn.ensure_loaded()
            return wn
        except Exception:
            return None

    def available(self) -> bool:
        return self._corpus() is not None

    def needs_install(self) -> bool:
        # A source install from before the tagger was added has the corpus only.
        return not self.available() or nltk_resources.pos_tagger_problem() is not None

    def pending_cost(self) -> str:
        if self.available():  # only the tagger is missing
            return "~1.5 MB (part-of-speech tagger)"
        return self.install_cost

    def install(self) -> None:
        try:
            import nltk
        except ImportError as e:
            import sys
            if getattr(sys, "frozen", False):  # pip cannot change a packaged app
                raise LookupFailed(
                    "This copy of the app is missing its WordNet support. Extract a "
                    "fresh copy of the Vocab Study ZIP.", recoverable=True) from e
            raise LookupFailed(
                "The 'nltk' package is not installed. Install the optional "
                "feature first:  pip install \"vocab-study[wordnet]\"",
                recoverable=True,
            ) from e
        for package in nltk_resources.NLTK_PACKAGES:
            nltk.download(package, quiet=True, raise_on_error=True)

    def lookup(self, word: str) -> List[Sense]:
        wn = self._corpus()
        if wn is None:
            raise LookupFailed(
                "WordNet is not installed. Run:  dict install wordnet",
                recoverable=True,
            )
        synsets = wn.synsets(word.strip().replace(" ", "_"))
        if not synsets:
            raise LookupFailed(f"'{word}' not found in WordNet.", recoverable=False)
        senses: List[Sense] = []
        for syn in synsets:  # all of them: the caller ranks, then trims
            examples = syn.examples()
            senses.append(Sense(
                definition=syn.definition().strip(),
                pos=_POS_MAP.get(syn.pos(), syn.pos()),
                example=examples[0] if examples else "",
            ))
        return senses
