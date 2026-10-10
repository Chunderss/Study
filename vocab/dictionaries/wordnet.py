"""WordNet backend (offline) via NLTK.

Optional feature. `available()` is False until both the `nltk` package and the
`wordnet` corpus are present. `install()` downloads nltk_resources.NLTK_PACKAGES
on demand: the corpus and the part-of-speech tagger that sense selection uses,
or only the tagger when the corpus already works. The CLI surfaces that cost
(`pending_cost()`) before calling it, and the desktop build bundles the same
packages (tools/build_desktop.py, VocabStudy.spec).
"""
from __future__ import annotations

import io
import socket
from typing import List

from .. import nltk_resources
from ..core.models import Sense
from .base import Dictionary, LookupFailed

_POS_MAP = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}

# The longest an install waits for any one connect or read. NLTK passes no
# timeout of its own, so a network that silently drops packets would stall
# the download (and the terminal's first start) forever. A slow but steady
# download is not cut off; DNS lookups keep the system resolver's timeouts.
DOWNLOAD_TIMEOUT = 30.0


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
        # What pending_cost() announced. NLTK would fetch WordNet again unless it
        # is in NLTK's download folder, even when it works from another one.
        packages = ([nltk_resources.POS_TAGGER] if self.available()
                    else list(nltk_resources.NLTK_PACKAGES))
        # NLTK opens its connections with the process's default timeout, so
        # that bounds every wait; it is restored however the install ends.
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(DOWNLOAD_TIMEOUT)
        try:
            for package in packages:
                # The error is raised, and callers report it: NLTK's own
                # "[nltk_data] Error loading ..." line would repeat it.
                nltk.download(package, quiet=True, raise_on_error=True,
                              print_error_to=io.StringIO())
        finally:
            socket.setdefaulttimeout(previous)

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
