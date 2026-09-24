"""Filesystem layout for the program.

Data root resolution order:
    1. $VOCAB_HOME (explicit override, mostly for tests)
    2. %LOCALAPPDATA%\\VocabStudy on Windows
    3. ~/.vocab elsewhere

Layout (module model — a Module is a study container of typed components)::

    <root>/
        config.json
        dictionaries/            installed dictionary data (e.g. downloaded .dict files)
        modules/
            <ModuleName>/
                module.json      manifest: name, enabled components, metadata
                vocab/
                    words.json   the importable word/definition payload
                    stats.json   per-word scheduler state (importable progress)
                notes/           markdown notes (*.md)
                documents/       PDF/EPUB and other reference files

Legacy layout (pre-module, auto-migrated on startup)::

    <root>/lists/<ListName>/{words.json, stats.json, sources/}
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

# Reserved as module names. STUDYALL is the ephemeral virtual session; the
# component directory names are reserved so a module can't collide with its own
# internals; the rest are Windows device names.
_RESERVED = {"con", "prn", "aux", "nul", "studyall",
             "sources", "vocab", "notes", "documents"}
_VALID = re.compile(r"^[A-Za-z0-9 _.\-()']+$")

# Component subdirectory names inside a module.
VOCAB_DIR = "vocab"
NOTES_DIR = "notes"
DOCUMENTS_DIR = "documents"


class InvalidListName(ValueError):
    """Kept for backwards compatibility; raised for bad module/list names."""


# New canonical alias.
InvalidModuleName = InvalidListName


def sanitize_list_name(name: str) -> str:
    """Validate a user-supplied module (formerly list) name. Raise on unsafe.

    Names double as directory names, so we block path traversal, reserved
    device/component names, and characters illegal on Windows.
    """
    name = (name or "").strip()
    if not name:
        raise InvalidModuleName("Name cannot be empty.")
    if len(name) > 100:
        raise InvalidModuleName("Name too long (max 100 chars).")
    if not _VALID.match(name):
        raise InvalidModuleName(
            "Name may only contain letters, numbers, spaces, and _ . - ( ) '"
        )
    validate_windows_filename(name)
    if name.lower() in _RESERVED:
        raise InvalidModuleName(f"'{name}' is a reserved name.")
    if name in (".", ".."):
        raise InvalidModuleName("Invalid name.")
    return name


def validate_windows_filename(name: str) -> None:
    if name.endswith("."):
        raise InvalidModuleName("Name cannot end with a dot.")
    device = name.split(".", 1)[0].lower()
    windows_devices = {"con", "prn", "aux", "nul"} | {
        f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)}
    if device in windows_devices:
        raise InvalidModuleName(f"'{name}' is a reserved Windows filename.")


# New canonical alias.
sanitize_module_name = sanitize_list_name


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def dictionaries_dir(self) -> Path:
        return self.root / "dictionaries"

    # ---- modules --------------------------------------------------------
    @property
    def modules_dir(self) -> Path:
        return self.root / "modules"

    def module_dir(self, name: str) -> Path:
        return self.modules_dir / sanitize_module_name(name)

    def manifest_file(self, name: str) -> Path:
        return self.module_dir(name) / "module.json"

    def vocab_dir(self, name: str) -> Path:
        return self.module_dir(name) / VOCAB_DIR

    def words_file(self, name: str) -> Path:
        return self.vocab_dir(name) / "words.json"

    def stats_file(self, name: str) -> Path:
        return self.vocab_dir(name) / "stats.json"

    def notes_dir(self, name: str) -> Path:
        return self.module_dir(name) / NOTES_DIR

    def documents_dir(self, name: str) -> Path:
        return self.module_dir(name) / DOCUMENTS_DIR

    def reading_pos_file(self, name: str) -> Path:
        """Per-module reading positions for documents (filename -> position)."""
        return self.module_dir(name) / "reading.json"

    # sources_dir retained as an alias of documents (legacy vocabulary).
    def sources_dir(self, name: str) -> Path:
        return self.documents_dir(name)

    # ---- legacy (pre-module) paths, for the migrator -------------------
    @property
    def legacy_lists_dir(self) -> Path:
        return self.root / "lists"

    # Backwards-compat property name used by older code/tests.
    @property
    def lists_dir(self) -> Path:
        return self.modules_dir

    def ensure_base(self) -> None:
        self.dictionaries_dir.mkdir(parents=True, exist_ok=True)
        self.modules_dir.mkdir(parents=True, exist_ok=True)

    def ensure_module(self, name: str) -> Path:
        d = self.module_dir(name)
        d.mkdir(parents=True, exist_ok=True)
        self.vocab_dir(name).mkdir(parents=True, exist_ok=True)
        self.notes_dir(name).mkdir(parents=True, exist_ok=True)
        self.documents_dir(name).mkdir(parents=True, exist_ok=True)
        return d

    # Legacy alias.
    def ensure_list(self, name: str) -> Path:
        return self.ensure_module(name)

    def list_dir(self, name: str) -> Path:
        return self.module_dir(name)


def _normalize_root(raw: str | os.PathLike) -> Path:
    """Resolve a user-supplied root, tolerating MSYS/git-bash style paths.

    On Windows, git-bash hands us paths like ``/c/Users/me/x`` or ``/tmp/x``.
    ``Path.resolve()`` would misread the leading ``/`` as the current drive root
    (creating e.g. ``C:\\c\\Users\\...``). Translate the common MSYS forms to a
    real Windows path first.
    """
    s = os.fspath(raw)
    if os.name == "nt" and s.startswith("/"):
        # /c/Users/... -> C:\Users\... ;  /tmp/... -> %TEMP%\... (best effort)
        parts = s.strip("/").split("/", 1)
        if len(parts) == 2 and len(parts[0]) == 1 and parts[0].isalpha():
            s = f"{parts[0].upper()}:\\" + parts[1].replace("/", "\\")
        elif parts and parts[0] == "tmp":
            base = os.environ.get("TEMP") or os.environ.get("TMP") or str(Path.home() / "AppData" / "Local" / "Temp")
            rest = parts[1] if len(parts) > 1 else ""
            s = str(Path(base) / rest.replace("/", "\\"))
    return Path(s).expanduser().resolve()


def _default_root() -> Path:
    override = os.environ.get("VOCAB_HOME")
    if override:
        return _normalize_root(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "VocabStudy"
    return Path.home() / ".vocab"


def get_paths(root: str | os.PathLike | None = None) -> Paths:
    p = Paths(_normalize_root(root) if root else _default_root())
    p.ensure_base()
    return p
