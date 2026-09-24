"""Command parsing.

Commands are shell-like: a verb followed by arguments. List names can contain
spaces, so we accept simple double-quoting for multi-word names::

    CREATE "The Great Gatsby"
    ADDL "The Great Gatsby" ephemeral

Verbs are case-insensitive; the rest is preserved as typed.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import List


@dataclass
class Command:
    verb: str            # upper-cased
    args: List[str]
    raw: str

    @property
    def arg_str(self) -> str:
        """Everything after the verb, un-split (for free-text like definitions)."""
        parts = self.raw.strip().split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ""


def parse(line: str) -> Command | None:
    line = line.strip()
    if not line:
        return None
    try:
        tokens = shlex.split(line, posix=True)
    except ValueError:
        # Unbalanced quotes — fall back to whitespace split so the user still
        # gets a sensible error from the handler rather than a crash.
        tokens = line.split()
    if not tokens:
        return None
    verb = tokens[0].upper()
    return Command(verb=verb, args=tokens[1:], raw=line)
