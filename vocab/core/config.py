"""Program configuration (active dictionary, active algorithm, judge backend).

Stored as a small JSON file at <root>/config.json. Defaults are chosen so the
program runs fully offline with zero optional downloads.
"""
from __future__ import annotations

import json
import os
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
        try:
            # The check itself can fail (e.g. a symlink into a folder the user
            # cannot open): such a file is unreadable, not missing.
            present = p.exists()
        except OSError as error:
            return cls._unreadable(p, error)
        if not present:
            cfg = cls()
            cfg.save(paths)  # a failed first write is reported as a write error
            return cfg
        try:
            # Windows editors often save UTF-8 with a BOM; accept it on read.
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError) as error:
            return cls._unreadable(p, error)
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)

    @classmethod
    def _unreadable(cls, path, error) -> "Config":
        # Run on defaults without touching the file, so it can be repaired.
        cfg = cls()
        cfg.load_error = (f"Could not read {path}: {error}. Using default settings "
                          "until it is fixed; the file was left unchanged.")
        return cfg

    def change(self, paths: Paths, **values) -> None:
        """Apply and save setting changes.

        While an unreadable config.json is still on disk, refuse instead of
        replacing it with defaults: the user repairs or deletes it deliberately.
        """
        self._check_writable(paths)
        for key, value in values.items():
            setattr(self, key, value)
        self.save(paths)

    def save(self, paths: Paths) -> None:
        from .storage import _atomic_write
        self._check_writable(paths)
        _atomic_write(paths.config_file, asdict(self))

    def _check_writable(self, paths: Paths) -> None:
        if not self.load_error:
            return
        try:
            present = paths.config_file.exists()
        except OSError:
            present = True  # cannot tell, so never treat it as removed
        if present:
            raise ValueError(f"Settings were not changed: {paths.config_file} could not be read "
                             "at startup. Fix or delete that file, then restart Vocab Study.")
        self.load_error = ""  # the file was removed, so saving starts a fresh one
