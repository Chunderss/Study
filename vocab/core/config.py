"""Program configuration (active dictionary, active algorithm, judge backend).

Stored as a small JSON file at <root>/config.json. Defaults are chosen so the
program runs fully offline with zero optional downloads.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import Paths


@dataclass
class Config:
    active_dictionary: str = "wordnet"    # offline default; falls back if not installed
    active_algorithm: str = "leitner"
    judge_backend: str = "keyword"        # MVP judge; embeddings/ollama are phase 2
    disambiguator: str = "nlp"            # sense selection in context: nlp | ollama | base
    ollama_model: str = "llama3.2:3b"     # model used when disambiguator == "ollama"
    mechanical_repetition: bool = False   # write-the-definition mode toggle

    # Not a field: set when config.json exists but could not be read.
    load_error = ""

    @classmethod
    def load(cls, paths: Paths) -> "Config":
        p = paths.config_file
        if not p.exists():
            cfg = cls()
            cfg.save(paths)
            return cfg
        try:
            # Windows editors often save UTF-8 with a BOM; accept it on read.
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError) as error:
            # Run on defaults without touching the file, so it can be repaired.
            cfg = cls()
            cfg.load_error = (f"Could not read {p}: {error}. Using default settings "
                              "until it is fixed; the file was left unchanged.")
            return cfg
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, paths: Paths) -> None:
        from .storage import _atomic_write
        p = paths.config_file
        if self.load_error and p.exists():
            # Saving settings replaces an unreadable file: keep a copy first.
            backup = p.with_name("config.unreadable.json")
            n = 1
            while backup.exists():
                backup = p.with_name(f"config.unreadable-{n}.json")
                n += 1
            shutil.copy2(p, backup)
            self.load_error = ""
        _atomic_write(p, asdict(self))
