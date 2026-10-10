"""Dictionary interface — the seam that lets 'many dictionaries, one active' work."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from ..core.models import Sense

#: Senses kept for a word. Callers trim to it only after ranking the senses for
#: the word's context, so a backend returns every sense it has.
SENSE_LIMIT = 6


class DictionaryError(Exception):
    pass


class LookupFailed(DictionaryError):
    """Raised when a word cannot be resolved (not found OR backend unavailable).

    ``recoverable`` distinguishes a genuine 'no such word' (False) from a
    transient failure such as a network timeout (True), so the CLI can tell the
    user to retry vs. offer to add the word with a manual definition.
    """
    def __init__(self, message: str, recoverable: bool = False):
        super().__init__(message)
        self.recoverable = recoverable


class Dictionary(ABC):
    #: stable id used in config + commands
    id: str = ""
    #: human label
    name: str = ""
    #: one-line description shown in `dict list`
    description: str = ""
    #: approximate install footprint shown BEFORE download, e.g. "~40 MB". ""=none.
    install_cost: str = ""
    #: whether the backend needs the network at lookup time
    requires_network: bool = False

    @abstractmethod
    def available(self) -> bool:
        """True if this backend can be used right now (installed / importable)."""

    def needs_install(self) -> bool:
        """True if install() would add something this backend is missing."""
        return not self.available()

    def pending_cost(self) -> str:
        """What install() would download now (install_cost unless partly installed)."""
        return self.install_cost

    @abstractmethod
    def install(self) -> None:
        """Download/prepare any required data. No-op for zero-install backends."""

    @abstractmethod
    def lookup(self, word: str) -> List[Sense]:
        """Return one or more Senses, or raise LookupFailed."""
