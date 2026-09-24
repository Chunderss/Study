"""WordNet backend (offline) via NLTK.

Optional feature. `available()` is False until both the `nltk` package and the
`wordnet` corpus are present. `install()` downloads the corpus (~40 MB) on
demand — the CLI surfaces that cost before calling it.
"""
from __future__ import annotations

from typing import List

from ..core.models import Sense
from .base import Dictionary, LookupFailed

_POS_MAP = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}


class WordNet(Dictionary):
    id = "wordnet"
    name = "WordNet (offline)"
    description = "Offline lexical database. Works with no network once installed."
    install_cost = "~11 MB (English WordNet corpus)"
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

    def install(self) -> None:
        try:
            import nltk
        except ImportError as e:
            raise LookupFailed(
                "The 'nltk' package is not installed. Install the optional "
                "feature first:  pip install \"vocab-study[wordnet]\"",
                recoverable=True,
            ) from e
        nltk.download("wordnet", quiet=True, raise_on_error=True)

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
        for syn in synsets[:6]:
            examples = syn.examples()
            senses.append(Sense(
                definition=syn.definition().strip(),
                pos=_POS_MAP.get(syn.pos(), syn.pos()),
                example=examples[0] if examples else "",
            ))
        return senses
