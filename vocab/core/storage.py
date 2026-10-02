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

import functools
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List

from .locking import write_lock
from .models import WordList
from .module import Module
from .paths import Paths, sanitize_module_name, validate_windows_filename


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


class DataFileError(ValueError):
    """A stored file exists but cannot be used. The file is left untouched."""

    def __init__(self, path, reason):
        super().__init__(f"Could not read {path}: {reason}")
        self.path = Path(path)
        self.reason = str(reason)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    # Windows editors often save UTF-8 with a BOM; accept it on read.
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataFileError(path, error) from error


def _read_json_object(path: Path, default):
    data = _read_json(path, default)
    if not isinstance(data, dict):
        raise DataFileError(path, "expected a JSON object")
    return data


class ModuleExists(Exception):
    pass


class ModuleNotFound(Exception):
    pass


def in_transaction(method):
    """Run a method holding the data folder's write lock (see Storage.transaction)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        storage = getattr(self, "storage", self)
        with storage.transaction():
            return method(self, *args, **kwargs)
    return wrapper


def _addressable(name: str) -> bool:
    try:
        return sanitize_module_name(name) == name
    except ValueError:
        return False


# Backwards-compatible aliases (old code/tests referenced these names).
ListExists = ModuleExists
ListNotFound = ModuleNotFound


class Storage:
    def __init__(self, paths: Paths):
        self.paths = paths

    def transaction(self):
        """Hold the data folder's write lock for a whole read-check-write
        sequence, so another process (desktop or CLI) cannot interleave and
        lose one of the changes. Re-entrant within a thread."""
        return write_lock(self.paths.root).hold()

    # ---- module lifecycle ----------------------------------------------
    def _module_dirs(self) -> List[str]:
        """Folder names that look like modules (a module.json OR a
        vocab/words.json, tolerating a half-written module)."""
        d = self.paths.modules_dir
        if not d.exists():
            return []
        names = []
        for p in d.iterdir():
            try:
                if not p.is_dir():
                    continue
                if (p / "module.json").exists() or (p / "vocab" / "words.json").exists():
                    names.append(p.name)
            except OSError:
                # A folder the user cannot open is still listed, so its loads
                # report the error instead of hiding or breaking every module.
                names.append(p.name)
        return sorted(names)

    def list_names(self) -> List[str]:
        """Names of all modules the app can address."""
        return [name for name in self._module_dirs() if _addressable(name)]

    def unsupported_module_dirs(self) -> List[str]:
        """Module folders renamed outside the app to a name it cannot use."""
        return [name for name in self._module_dirs() if not _addressable(name)]

    # New canonical alias.
    module_names = list_names

    def canonical_name(self, name: str) -> str:
        """Return the stored spelling without changing filesystem case rules."""
        name = sanitize_module_name(name)
        if not self.exists(name):
            raise ModuleNotFound(f"Module '{name}' does not exist.")
        names = self.list_names()
        if name in names:
            return name
        return next((stored for stored in names if stored.casefold() == name.casefold()), name)

    def exists(self, name: str) -> bool:
        name = sanitize_module_name(name)
        # Let the filesystem apply its own case rules (especially on Windows).
        return (self.paths.manifest_file(name).is_file()
                or self.paths.words_file(name).is_file())

    def load_module(self, name: str) -> Module:
        name = sanitize_module_name(name)
        if not self.exists(name):
            raise ModuleNotFound(f"Module '{name}' does not exist.")
        path = self.paths.manifest_file(name)
        if not path.exists():
            # tolerate a module with no manifest yet (e.g. freshly migrated)
            mod = Module(name=name)
            self.save_module(mod)
            return mod
        # A manifest that exists but holds JSON null or a list is damaged, not absent.
        raw = _read_json_object(path, None)
        try:
            mod = Module.from_dict(raw, fallback_name=name)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise DataFileError(path, f"unexpected content ({error})") from error
        mod.name = name  # directory identity is authoritative
        return mod

    def save_module(self, mod: Module) -> None:
        _atomic_write(self.paths.manifest_file(mod.name), mod.to_dict())

    @in_transaction
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

    @in_transaction
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
        path = self.paths.words_file(name)
        raw = _read_json_object(path, {"name": name, "words": {}})
        try:
            wl = WordList.from_dict(raw, fallback_name=name)
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise DataFileError(path, f"unexpected content ({error})") from error
        wl.name = name
        return wl

    def save_words(self, wl: WordList) -> None:
        _atomic_write(self.paths.words_file(wl.name), wl.to_dict())

    # ---- stats (scheduler state) ---------------------------------------
    def load_stats(self, name: str) -> Dict[str, dict]:
        return _read_json_object(self.paths.stats_file(sanitize_module_name(name)), {})

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
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return p.stem

    @in_transaction
    def create_note(self, module: str, note: str) -> str:
        if not self.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        p = self._note_path(module, note)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation prevents accidental clobbering, also on Windows.
        with p.open("x", encoding="utf-8"):
            pass
        return p.stem

    @in_transaction
    def delete_note(self, module: str, note: str) -> None:
        p = self._note_path(module, note)
        if not p.exists():
            raise ModuleNotFound(f"Note '{note}' does not exist in '{module}'.")
        p.unlink()
        self.clear_note_draft(module, note)

    # Recovery copies are separate from Markdown files so Save/Discard keeps
    # its usual meaning. Store the baseline too, for external-edit detection.
    def _draft_path(self, module, note):
        return self.paths.module_dir(sanitize_module_name(module)) / "drafts" / f"{_sanitize_note_name(note)}.json"

    def save_note_draft(self, module, note, baseline, content):
        if not self.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        _atomic_write(self._draft_path(module, note), {"baseline": baseline, "content": content})

    def clear_note_draft(self, module, note):
        self._draft_path(module, note).unlink(missing_ok=True)

    def note_drafts(self, module):
        directory = self.paths.module_dir(sanitize_module_name(module)) / "drafts"
        return sorted(p.stem for p in directory.glob("*.json"))

    def load_note_draft(self, module, note):
        data = _read_json(self._draft_path(module, note), None)
        if data is not None and (not isinstance(data, dict) or
                not all(isinstance(data.get(k), str) for k in ("baseline", "content"))):
            raise ValueError(f"Unreadable recovery copy for {module}/{note}; file kept on disk.")
        return data

    # ---- documents (documents component) -------------------------------
    def document_names(self, module: str) -> List[str]:
        d = self.paths.documents_dir(sanitize_module_name(module))
        if not d.exists():
            return []
        return sorted(p.name for p in d.iterdir() if p.is_file())

    @in_transaction
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
        if dest.exists():
            raise FileExistsError(f"Document '{src.name}' already exists in '{module}'.")
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

    @in_transaction
    def save_reading_pos(self, module: str, filename: str, pos: dict) -> None:
        """Persist where the reader left off in `filename` (chapter/scroll/zoom)."""
        module = sanitize_module_name(module)
        if not self.exists(module):
            return  # an old reader callback must not recreate a deleted module
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

    @in_transaction
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
    validate_windows_filename(note)
    return note
