"""Persistence layer: atomic JSON I/O, module lifecycle, notes, import/export.

Every write goes through ``_atomic_write`` (temp file + os.replace) so a crash
mid-write can never corrupt data. Words and stats live in separate files inside
a module's vocab/ component so progress and definitions can be shared
independently.

The public vocab method names (load_words/save_words/load_stats/save_stats/
list_names/create/delete/exists) are unchanged from the pre-module version — they
now operate on modules/<name>/vocab/ — so the rest of the app is unaffected by
the module refactor.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List

from .models import WordList
from .module import Module
from .paths import Paths, sanitize_module_name


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class ModuleExists(Exception):
    pass


class ModuleNotFound(Exception):
    pass


# Backwards-compatible aliases (old code/tests referenced these names).
ListExists = ModuleExists
ListNotFound = ModuleNotFound


class Storage:
    def __init__(self, paths: Paths):
        self.paths = paths

    # ---- module lifecycle ----------------------------------------------
    def list_names(self) -> List[str]:
        """Names of all modules (a module is any dir with a module.json OR a
        vocab/words.json, tolerating a half-written module)."""
        d = self.paths.modules_dir
        if not d.exists():
            return []
        names = []
        for p in d.iterdir():
            if not p.is_dir():
                continue
            if (p / "module.json").exists() or (p / "vocab" / "words.json").exists():
                names.append(p.name)
        return sorted(names)

    # New canonical alias.
    module_names = list_names

    def exists(self, name: str) -> bool:
        return sanitize_module_name(name) in self.list_names()

    def load_module(self, name: str) -> Module:
        name = sanitize_module_name(name)
        if not self.exists(name):
            raise ModuleNotFound(f"Module '{name}' does not exist.")
        raw = _read_json(self.paths.manifest_file(name), None)
        if raw is None:
            # tolerate a module with no manifest yet (e.g. freshly migrated)
            mod = Module(name=name)
            self.save_module(mod)
            return mod
        return Module.from_dict(raw, fallback_name=name)

    def save_module(self, mod: Module) -> None:
        _atomic_write(self.paths.manifest_file(mod.name), mod.to_dict())

    def create(self, name: str) -> WordList:
        """Create a module (with its vocab component) and return the empty
        WordList — signature preserved from the pre-module API."""
        name = sanitize_module_name(name)
        if self.exists(name):
            raise ModuleExists(f"Module '{name}' already exists.")
        self.paths.ensure_module(name)
        self.save_module(Module(name=name))
        wl = WordList(name=name)
        self.save_words(wl)
        self.save_stats(name, {})
        return wl

    # New canonical alias.
    create_module = create

    def delete(self, name: str) -> None:
        name = sanitize_module_name(name)
        if not self.exists(name):
            raise ModuleNotFound(f"Module '{name}' does not exist.")
        shutil.rmtree(self.paths.module_dir(name))

    delete_module = delete

    # ---- words (vocab component) ---------------------------------------
    def load_words(self, name: str) -> WordList:
        name = sanitize_module_name(name)
        if not self.exists(name):
            raise ModuleNotFound(f"Module '{name}' does not exist.")
        raw = _read_json(self.paths.words_file(name), {"name": name, "words": {}})
        return WordList.from_dict(raw, fallback_name=name)

    def save_words(self, wl: WordList) -> None:
        _atomic_write(self.paths.words_file(wl.name), wl.to_dict())

    # ---- stats (scheduler state) ---------------------------------------
    def load_stats(self, name: str) -> Dict[str, dict]:
        return _read_json(self.paths.stats_file(sanitize_module_name(name)), {})

    def save_stats(self, name: str, stats: Dict[str, dict]) -> None:
        _atomic_write(self.paths.stats_file(sanitize_module_name(name)), stats)

    # ---- notes (notes component) ---------------------------------------
    def _note_path(self, module: str, note: str) -> Path:
        module = sanitize_module_name(module)
        stem = _sanitize_note_name(note)
        return self.paths.notes_dir(module) / f"{stem}.md"

    def note_names(self, module: str) -> List[str]:
        d = self.paths.notes_dir(sanitize_module_name(module))
        if not d.exists():
            return []
        return sorted(p.stem for p in d.glob("*.md"))

    def load_note(self, module: str, note: str) -> str:
        p = self._note_path(module, note)
        if not p.exists():
            raise ModuleNotFound(f"Note '{note}' does not exist in '{module}'.")
        return p.read_text(encoding="utf-8")

    def save_note(self, module: str, note: str, content: str) -> str:
        module = sanitize_module_name(module)
        if not self.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        p = self._note_path(module, note)
        p.parent.mkdir(parents=True, exist_ok=True)
        # atomic text write
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        return p.stem

    def delete_note(self, module: str, note: str) -> None:
        p = self._note_path(module, note)
        if not p.exists():
            raise ModuleNotFound(f"Note '{note}' does not exist in '{module}'.")
        p.unlink()

    # ---- documents (documents component) -------------------------------
    def document_names(self, module: str) -> List[str]:
        d = self.paths.documents_dir(sanitize_module_name(module))
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file())

    def add_document(self, module: str, src: Path) -> str:
        module = sanitize_module_name(module)
        if not self.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        src = Path(src)
        if not src.exists():
            raise ModuleNotFound(f"File not found: {src}")
        dest_dir = self.paths.documents_dir(module)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        return dest.name

    # ---- reading position (per document) -------------------------------
    def load_reading_pos(self, module: str, filename: str) -> dict:
        """Return the saved reading position for a document, or {} if none."""
        data = _read_json(self.paths.reading_pos_file(sanitize_module_name(module)), {})
        if not isinstance(data, dict):
            return {}
        pos = data.get(filename)
        return pos if isinstance(pos, dict) else {}

    def save_reading_pos(self, module: str, filename: str, pos: dict) -> None:
        """Persist where the reader left off in `filename` (chapter/scroll/zoom)."""
        module = sanitize_module_name(module)
        path = self.paths.reading_pos_file(module)
        data = _read_json(path, {})
        if not isinstance(data, dict):
            data = {}
        data[filename] = pos
        _atomic_write(path, data)

    # ---- import / export (vocab bundle) --------------------------------
    def export_list(self, name: str, dest: Path, include_stats: bool = False) -> Path:
        """Write a portable JSON bundle of a module's vocab for sharing."""
        wl = self.load_words(name)
        bundle = {"words": wl.to_dict()}
        if include_stats:
            bundle["stats"] = self.load_stats(name)
        _atomic_write(Path(dest), bundle)
        return Path(dest)

    def import_list(self, src: Path, new_name: str | None = None,
                    with_stats: bool = False) -> str:
        """Import a vocab bundle (or a bare words.json) as a NEW module."""
        raw = _read_json(Path(src), None)
        if raw is None:
            raise ModuleNotFound(f"Import file not found: {src}")
        words_payload = raw.get("words") if "words" in raw and "schema" not in raw else raw
        wl = WordList.from_dict(words_payload)
        wl.name = sanitize_module_name(new_name or wl.name or Path(src).stem)
        if self.exists(wl.name):
            raise ModuleExists(f"Module '{wl.name}' already exists; choose a new name.")
        self.paths.ensure_module(wl.name)
        self.save_module(Module(name=wl.name))
        self.save_words(wl)
        stats = raw.get("stats", {}) if (with_stats and isinstance(raw, dict)) else {}
        self.save_stats(wl.name, stats if isinstance(stats, dict) else {})
        return wl.name


def _sanitize_note_name(note: str) -> str:
    """Note names double as filenames; keep them safe and simple."""
    import re
    note = (note or "").strip()
    if not note:
        raise ValueError("Note name cannot be empty.")
    if note.lower().endswith(".md"):
        note = note[:-3]
    if not re.match(r"^[A-Za-z0-9 _.\-()']+$", note):
        raise ValueError("Note name may only contain letters, numbers, spaces, and _ . - ( ) '")
    if len(note) > 100:
        raise ValueError("Note name too long (max 100 chars).")
    if note in (".", ".."):
        raise ValueError("Invalid note name.")
    return note
