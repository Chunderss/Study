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

    @classmethod
    def load(cls, paths: Paths) -> "Config":
        p = paths.config_file
        if not p.exists():
            cfg = cls()
            cfg.save(paths)
            return cfg
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        known = {k: data[k] for k in data if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, paths: Paths) -> None:
        path = paths.config_file
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2)
        os.replace(tmp, path)
