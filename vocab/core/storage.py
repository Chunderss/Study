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
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Dict, List

from .locking import DRAFT_TIMEOUT, WRITE_TIMEOUT, write_lock
from .models import SCHEMA_VERSION, Word, WordList
from .module import Module
from .paths import InvalidListName, Paths, sanitize_module_name, validate_windows_filename


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


# Application-owned temporary names (prefix + 32 hex digits); never listed as
# modules or documents. Only that exact form is hidden or purged, so a user's
# file or module that merely starts the same way stays visible and safe.
STAGING_PREFIX = ".vocab-staging-"
TRASH_PREFIX = ".vocab-trash-"
_LEFTOVER = re.compile(r"^\.vocab-(staging|trash)-[0-9a-f]{32}$")
_STALE_STAGING = 24 * 3600  # an unfinished copy this old is abandoned


def _leftover_kind(name: str):
    """'staging' or 'trash' for an app-owned temporary name, else None."""
    match = _LEFTOVER.match(name)
    return match.group(1) if match else None


_JUNCTION = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)


def _is_link(path) -> bool:
    """A symlink, or a Windows junction: removing it must not touch its target."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (stat.S_ISLNK(st.st_mode)
            or getattr(st, "st_reparse_tag", None) == _JUNCTION)


def _remove_tree(folder: Path) -> None:
    """Remove an app-owned folder. A read-only entry inside it is made writable
    and retried; links are removed, never followed, and nothing outside the
    folder has its permissions changed."""
    root = os.path.abspath(folder)

    def inside(path):
        path = os.path.abspath(path)
        return path == root or path.startswith(root + os.sep)

    def make_writable(function, path, error):
        error = error if isinstance(error, BaseException) else error[1]
        if not isinstance(error, PermissionError):
            raise error
        # POSIX refuses to remove from a read-only folder; Windows refuses a read-only file.
        parent = os.path.dirname(path)
        if inside(parent) and not _is_link(parent):
            os.chmod(parent, stat.S_IRWXU)
        if inside(path) and not _is_link(path):
            os.chmod(path, stat.S_IRWXU if os.path.isdir(path) else stat.S_IREAD | stat.S_IWRITE)
        function(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=make_writable)
    else:
        shutil.rmtree(folder, onerror=make_writable)


def _remove_link(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)  # a Windows junction or directory symlink


def _publish_file(staging: Path, dest: Path, exists_message: str) -> None:
    """Move a finished file into place without replacing one that appeared."""
    try:
        os.link(staging, dest)  # refuses an existing destination
        return
    except FileExistsError:
        raise FileExistsError(exists_message) from None
    except OSError:
        pass  # no hard links on this filesystem (FAT, some network drives)
    if dest.exists():
        raise FileExistsError(exists_message)
    os.rename(staging, dest)  # under the write lock; Windows refuses an existing target


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


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


def _word_entry_problem(key, entry):
    if not isinstance(key, str) or not key.strip():
        return "the word is empty"
    if not isinstance(entry, dict):
        return "its entry is not an object"
    senses = entry.get("senses")
    if not isinstance(senses, list) or not senses:
        return "it has no definitions"
    for sense in senses:
        if not isinstance(sense, dict):
            return "a definition is not an object"
        if not isinstance(sense.get("definition"), str) or not sense["definition"].strip():
            return "a definition is empty"
        for field in ("pos", "example"):
            if field in sense and not isinstance(sense[field], str):
                return f"'{field}' must be text"
    for field in ("dictionary", "added"):
        if field in entry and not isinstance(entry[field], str):
            return f"'{field}' must be text"
    return None


def parse_import(raw, *, with_stats=False, card_problem=None):
    """Validate an import file and return (WordList, progress).

    Accepted: an export bundle {"words": <words.json>, "stats": {...}}, and a
    module's words.json, with or without its "schema" field. Anything else,
    and any unusable entry or progress card, raises ValueError.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("words"), dict):
        raise ValueError("it is not a Vocab Study export or words.json (no \"words\" object)")
    inner = raw["words"]
    nested = inner.get("words")
    # A bundle has no top-level "schema" and its inner words.json has its own
    # "words" map. In a bare words.json that is an entry, with a "senses" list;
    # in a bundle "senses" can only be a word (an entry object).
    if ("schema" not in raw and isinstance(nested, dict)
            and not isinstance(nested.get("senses"), list)):
        document, progress = inner, raw.get("stats")
    else:
        document, progress = raw, None
    schema = document.get("schema", SCHEMA_VERSION)
    if isinstance(schema, bool) or not isinstance(schema, int) or not 1 <= schema <= SCHEMA_VERSION:
        raise ValueError(f"its format version {schema!r} is not supported by this version of the app")
    entries = document.get("words")
    if not isinstance(entries, dict):
        raise ValueError("its \"words\" value is not an object")
    name = document.get("name")
    wl = WordList(name=name.strip() if isinstance(name, str) else "")
    spellings = {}
    for key, entry in entries.items():
        problem = _word_entry_problem(key, entry)
        if problem:
            raise ValueError(f"word {key!r}: {problem}")
        normalized = wl.normalize_key(key)
        if normalized in spellings:
            raise ValueError(f"{spellings[normalized]!r} and {key!r} are the same word")
        spellings[normalized] = key
        wl.words[normalized] = Word.from_dict(key, entry)
    stats = {}
    if with_stats:
        if not isinstance(progress, dict):
            raise ValueError("it has no progress to import; import it without --stats")
        check = card_problem or (lambda card: None if isinstance(card, dict) else "progress is not an object")
        for key, card in progress.items():
            normalized = str(key).strip().lower()
            if normalized not in wl.words:
                continue  # progress for a word that is not in the file
            problem = check(card)
            if problem:
                raise ValueError(f"progress for {key!r}: {problem}")
            stats[normalized] = card
    return wl, stats


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

    def transaction(self, timeout: float = WRITE_TIMEOUT):
        """Hold the data folder's write lock for a whole read-check-write
        sequence, so another process (desktop or CLI) cannot interleave and
        lose one of the changes. Re-entrant within a thread."""
        return write_lock(self.paths.root).hold(timeout)

    # ---- module lifecycle ----------------------------------------------
    def _module_dirs(self) -> List[str]:
        """Folder names that look like modules (a module.json OR a
        vocab/words.json, tolerating a half-written module)."""
        d = self.paths.modules_dir
        if not d.exists():
            return []
        names = []
        for p in d.iterdir():
            if _leftover_kind(p.name):
                continue  # application-owned leftovers, never modules
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

    def module_identity(self, name: str):
        """Tells a module apart from a later one created under the same name.

        Read from the manifest as stored: parsing fills a missing timestamp with
        the current time, which would make the identity change on every load.
        """
        name = sanitize_module_name(name)
        path = self.paths.manifest_file(name)
        if not path.exists():
            self.load_module(name)  # writes a manifest once, so later reads agree
        try:
            raw = _read_json_object(path, {})
        except DataFileError:
            # Left as is; its bytes still tell this module from a recreated one.
            return ("unreadable", hashlib.sha256(path.read_bytes()).hexdigest())
        metadata = raw.get("metadata")
        return (raw.get("created"), metadata.get("id") if isinstance(metadata, dict) else None)

    def raw_word_entry(self, module: str, word: str):
        """A word's entry exactly as stored, without defaults filled in by
        parsing (a legacy entry without "added" would differ on every load)."""
        raw = _read_json_object(self.paths.words_file(sanitize_module_name(module)), {})
        words = raw.get("words")
        key = word.strip().lower()
        if isinstance(words, dict):
            for stored, entry in words.items():
                if str(stored).strip().lower() == key:
                    return entry
        return None

    def save_module(self, mod: Module) -> None:
        _atomic_write(self.paths.manifest_file(mod.name), mod.to_dict())

    @in_transaction
    def create(self, name: str) -> WordList:
        """Create a module (with its vocab component) and return the empty
        WordList — signature preserved from the pre-module API."""
        name = sanitize_module_name(name)
        if self.exists(name):
            raise ModuleExists(f"Module '{name}' already exists.")
        wl = WordList(name=name)
        self._publish_new_module(wl, {})
        return wl

    # New canonical alias.
    create_module = create

    @in_transaction
    def delete(self, name: str) -> None:
        """Delete a module: first move its whole folder to a hidden trash name
        in one step, then remove it. If the move fails (on Windows, while a
        file inside is open) nothing was deleted; if the removal fails, the
        module is already gone and the leftover is purged at a later start."""
        name = sanitize_module_name(name)
        if not self.exists(name):
            raise ModuleNotFound(f"Module '{name}' does not exist.")
        folder = self.paths.module_dir(name)
        if _is_link(folder):
            raise OSError(f"Could not delete '{name}': its folder is a link to "
                          f"{os.path.realpath(folder)}. Remove the link yourself if you no "
                          "longer want it; nothing was deleted.")
        trash = self.paths.modules_dir / f"{TRASH_PREFIX}{uuid.uuid4().hex}"
        try:
            folder.rename(trash)
        except OSError as error:
            raise OSError(f"Could not delete '{name}': {error.strerror or error}. Close any "
                          "document from this module and try again; nothing was deleted.") from error
        try:
            _remove_tree(trash)
        except OSError:
            pass  # hidden, and purged by purge_leftovers()

    def purge_leftovers(self) -> None:
        """Remove what interrupted operations left behind: trashed modules and,
        once clearly abandoned, unfinished staging copies."""
        stale = time.time() - _STALE_STAGING
        folders = [self.paths.modules_dir]
        try:
            folders += [self.paths.documents_dir(name) for name in self.list_names()]
        except OSError:
            pass
        for folder in folders:
            try:
                entries = list(folder.iterdir())
            except OSError:
                continue  # one unreadable folder must not stop the rest
            for path in entries:
                kind = _leftover_kind(path.name)
                try:
                    if kind is None or (kind == "staging" and path.lstat().st_mtime >= stale):
                        continue  # not ours, or possibly a copy still in progress
                    if _is_link(path):
                        _remove_link(path)
                    elif path.is_dir():
                        _remove_tree(path)
                    else:
                        path.unlink()
                except OSError:
                    pass

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

    @in_transaction
    def save_words_and_stats(self, wl: WordList, stats: Dict[str, dict]) -> None:
        """Write both files of one vocabulary change. If the progress write
        fails, words.json gets its previous bytes back, so a module never
        keeps half of a change."""
        words = self.paths.words_file(wl.name)
        previous = words.read_bytes() if words.exists() else None
        self.save_words(wl)
        try:
            self.save_stats(wl.name, stats)
        except BaseException:
            if previous is None:
                words.unlink(missing_ok=True)
            else:
                _atomic_write_bytes(words, previous)
            raise

    # ---- stats (scheduler state) ---------------------------------------
    def load_stats(self, name: str) -> Dict[str, dict]:
        return _read_json_object(self.paths.stats_file(sanitize_module_name(name)), {})

    def save_stats(self, name: str, stats: Dict[str, dict]) -> None:
        _atomic_write(self.paths.stats_file(sanitize_module_name(name)), stats)

    # ---- notes (notes component) ---------------------------------------
    def _note_path(self, module: str, note: str) -> Path:
        module = sanitize_module_name(module)
        directory = self.paths.notes_dir(module)
        # A file saved under a name that is not canonical ("Double.md.md",
        # "Space .md") stays reachable by the name note_names() lists for it,
        # spaces included ("Double.md", "Space ").
        for stored in dict.fromkeys((note or "", (note or "").strip())):
            if _stored_note_stem(stored) and (directory / f"{stored}.md").is_file():
                return directory / f"{stored}.md"
        return directory / f"{_sanitize_note_name(note)}.md"

    def canonical_note_name(self, module: str, note: str) -> str:
        """The stored spelling of a note.

        On a case-insensitive filesystem (Windows) 'one' opens 'One.md'; open
        buffers are keyed by the stored name, so callers must use this one.
        Distinct files that differ only in case (Linux) stay distinct.
        """
        path = self._note_path(module, note)
        stem = path.stem
        if path.exists():
            for candidate in path.parent.glob("*.md"):
                if candidate.stem.casefold() == stem.casefold() and os.path.samefile(candidate, path):
                    return candidate.stem
        return stem

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
        draft = self._draft_path(module, p.stem)  # the deleted note's own draft
        p.unlink()
        draft.unlink(missing_ok=True)

    # Recovery copies are separate from Markdown files so Save/Discard keeps
    # its usual meaning. Store the baseline too, for external-edit detection.
    def _draft_path(self, module, note):
        # Callers pass a note's stored name (from note_names or create_note), so
        # use it as is: drafts of "Double.md", "Space " and "Double" never share
        # a file, even after one note's Markdown file is gone.
        stem = note if _stored_note_stem(note) else _sanitize_note_name(note)
        return self.paths.module_dir(sanitize_module_name(module)) / "drafts" / f"{stem}.json"

    def save_note_draft(self, module, note, baseline, content):
        # Locked so a module deleted by another process is not recreated here.
        with self.transaction(DRAFT_TIMEOUT):
            if not self.exists(module):
                raise ModuleNotFound(f"Module '{module}' does not exist.")
            _atomic_write(self._draft_path(module, note), {"baseline": baseline, "content": content})

    def clear_note_draft(self, module, note):
        with self.transaction(DRAFT_TIMEOUT):
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
        return sorted(p.name for p in d.iterdir()
                      if p.is_file() and not _leftover_kind(p.name))

    def add_document(self, module: str, src: Path) -> str:
        """Copy a document into a module.

        The copy is written under a hidden staging name beside its final one
        and published only once complete, so a failed copy leaves nothing that
        blocks a retry. The write lock is held to check and publish, not during
        a long copy.
        """
        module = sanitize_module_name(module)
        if not self.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        src = Path(src)
        if not src.is_file():
            raise ModuleNotFound(f"File not found: {src}")
        if _leftover_kind(src.name):
            raise ValueError(f"'{src.name}' is a name Vocab Study reserves for its temporary "
                             "files; rename the file and add it again.")
        dest_dir = self.paths.documents_dir(module)
        dest_dir.mkdir(exist_ok=True)  # never recreates a module deleted meanwhile
        dest = dest_dir / src.name
        exists = f"Document '{src.name}' already exists in '{module}'."
        if dest.exists():
            raise FileExistsError(exists)
        staging = dest_dir / f"{STAGING_PREFIX}{uuid.uuid4().hex}"
        try:
            # Contents only: a read-only source must not make the copy read-only.
            shutil.copyfile(src, staging)
            with open(staging, "rb+") as copy:
                os.fsync(copy.fileno())
            with self.transaction():
                if not self.exists(module):
                    raise ModuleNotFound(f"Module '{module}' does not exist.")
                if dest.exists():
                    raise FileExistsError(exists)
                _publish_file(staging, dest, exists)
        finally:
            staging.unlink(missing_ok=True)
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

    def import_list(self, src: Path, new_name: str | None = None,
                    with_stats: bool = False, card_problem=None) -> str:
        """Import an export bundle or a words.json as a NEW module.

        The whole file is validated first, and the module is published only
        once complete, so a rejected or failed import leaves nothing behind.
        ``card_problem`` is the scheduler's progress check.
        """
        raw = _read_json(Path(src), None)  # the source file is only read
        if raw is None:
            raise ModuleNotFound(f"Import file not found: {src}")
        try:
            wl, stats = parse_import(raw, with_stats=with_stats, card_problem=card_problem)
        except ValueError as error:
            raise ValueError(f"Cannot import {src}: {error}. Nothing was imported.") from None
        wl.name = sanitize_module_name(new_name or wl.name or Path(src).stem)
        with self.transaction():
            if self.exists(wl.name):
                raise ModuleExists(f"Module '{wl.name}' already exists; choose a new name.")
            self._publish_new_module(wl, stats)
        return wl.name

    def _publish_new_module(self, wl: WordList, stats: Dict[str, dict]) -> None:
        """Build a module in a hidden staging folder, then move it into place in
        one step: a failure part way leaves no half-made module to block a retry."""
        if _leftover_kind(wl.name):
            raise InvalidListName(f"'{wl.name}' is reserved for Vocab Study's temporary folders.")
        self.paths.modules_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".vocab-staging-", dir=self.paths.root) as temporary:
            staged = Storage(Paths(Path(temporary)))
            staged.paths.ensure_module(wl.name)
            staged.save_module(Module(name=wl.name, metadata={"id": uuid.uuid4().hex}))
            staged.save_words(wl)
            staged.save_stats(wl.name, stats)
            staged.paths.module_dir(wl.name).rename(self.paths.module_dir(wl.name))


_NOTE_CHARS = re.compile(r"^[A-Za-z0-9 _.\-()']+$")


def _stored_note_stem(note) -> bool:
    """Whether ``note`` can be a stored note's file stem exactly as given."""
    return (isinstance(note, str) and note.strip() not in ("", ".", "..")
            and _NOTE_CHARS.match(note) is not None)


def _sanitize_note_name(note: str) -> str:
    """Note names double as filenames; keep them safe and simple.

    Idempotent: a returned name normalizes to itself, so it reopens the same
    note ("Space .md" -> "Space", "Double.md.md" -> "Double").
    """
    note = (note or "").strip()
    while note.lower().endswith(".md"):
        note = note[:-3].rstrip()
    if not note:
        raise ValueError("Note name cannot be empty.")
    if not _NOTE_CHARS.match(note):
        raise ValueError("Note name may only contain letters, numbers, spaces, and _ . - ( ) '")
    if len(note) > 100:
        raise ValueError("Note name too long (max 100 chars).")
    if note in (".", ".."):
        raise ValueError("Invalid note name.")
    validate_windows_filename(note)
    return note
