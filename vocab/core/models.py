"""Data models and the on-disk JSON schema.

The word payload stays close to the shape from the original spec, extended so a
word can carry multiple senses (real dictionaries have several)::

    {
      "schema": 1,
      "name": "The Great Gatsby",
      "words": {
        "ephemeral": {
          "dictionary": "wordnet",
          "added": "2026-09-01T12:00:00Z",
          "senses": [
            {"pos": "adjective", "definition": "lasting a very short time"}
          ]
        }
      }
    }

Scheduler state lives in a SEPARATE stats.json so progress is importable on its
own and the words payload stays a clean, shareable dictionary.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

SCHEMA_VERSION = 1


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Sense:
    definition: str
    pos: str = ""            # part of speech, e.g. "noun"; "" when unknown
    example: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Sense":
        return cls(
            definition=str(d.get("definition", "")).strip(),
            pos=str(d.get("pos", "")).strip(),
            example=str(d.get("example", "")).strip(),
        )


@dataclass
class Word:
    word: str
    dictionary: str = ""     # which dictionary provided the senses
    added: str = field(default_factory=utcnow_iso)
    senses: List[Sense] = field(default_factory=list)

    def primary_definition(self) -> str:
        return self.senses[0].definition if self.senses else ""

    def to_dict(self) -> dict:
        return {
            "dictionary": self.dictionary,
            "added": self.added,
            "senses": [asdict(s) for s in self.senses],
        }

    @classmethod
    def from_dict(cls, key: str, d: dict) -> "Word":
        return cls(
            word=key,
            dictionary=str(d.get("dictionary", "")),
            added=str(d.get("added", "")) or utcnow_iso(),
            senses=[Sense.from_dict(s) for s in d.get("senses", [])],
        )


@dataclass
class WordList:
    name: str
    words: Dict[str, Word] = field(default_factory=dict)

    def normalize_key(self, word: str) -> str:
        return word.strip().lower()

    def add(self, word: Word) -> None:
        self.words[self.normalize_key(word.word)] = word

    def remove(self, word: str) -> bool:
        return self.words.pop(self.normalize_key(word), None) is not None

    def has(self, word: str) -> bool:
        return self.normalize_key(word) in self.words

    def to_dict(self) -> dict:
        return {
            "schema": SCHEMA_VERSION,
            "name": self.name,
            "words": {k: w.to_dict() for k, w in sorted(self.words.items())},
        }

    @classmethod
    def from_dict(cls, d: dict, fallback_name: str = "") -> "WordList":
        wl = cls(name=str(d.get("name", fallback_name)))
        for key, wd in d.get("words", {}).items():
            wl.words[wl.normalize_key(key)] = Word.from_dict(key, wd)
        return wl
