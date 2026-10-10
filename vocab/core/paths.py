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
import sys
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


# ---- the folder for files ------------------------------------------------
# Relative paths typed in EXPORT, IMPORT and the backup commands start in one
# fixed folder, the user's Documents folder unless they choose another, rather
# than in the working directory, which depends on how the program was started
# (from Explorer, the app's own folder, which an update replaces).

def _is_folder(path) -> bool:
    try:
        return Path(path).is_dir()
    except OSError:
        return False


def _windows_documents():
    """The Documents known folder, which follows OneDrive and folder
    redirection, or None."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
    folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                     (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
    # Own library objects, so these prototypes change nothing shared.
    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    get_path = shell32.SHGetKnownFolderPath
    get_path.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                         ctypes.POINTER(ctypes.c_void_p)]
    get_path.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    buffer = ctypes.c_void_p()
    try:
        if get_path(ctypes.byref(folder_id), 0, None, ctypes.byref(buffer)) != 0:
            return None
        return Path(ctypes.wstring_at(buffer.value))
    finally:
        ole32.CoTaskMemFree(buffer)


def _xdg_documents(home: Path):
    """XDG_DOCUMENTS_DIR from user-dirs.dirs, or None when it is not set."""
    config = os.environ.get("XDG_CONFIG_HOME", "")
    config = Path(config) if os.path.isabs(config) else home / ".config"
    try:
        text = (config / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        name, _, value = line.strip().partition("=")
        if name != "XDG_DOCUMENTS_DIR":
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        if value == "$HOME" or value.startswith("$HOME/"):
            return Path(str(home) + value[len("$HOME"):])  # "$HOME/" alone: disabled, so home
        if value.startswith("/"):
            return Path(value)
        return None
    return None


def platform_documents_folder() -> Path:
    """The user's Documents folder, or the home folder when there is none.

    Windows: the Documents known folder, then %USERPROFILE%\\Documents.
    Linux and BSD: XDG_DOCUMENTS_DIR from user-dirs.dirs. Then ~/Documents.
    """
    home = Path.home()
    candidates = []
    if os.name == "nt":
        try:
            candidates.append(_windows_documents())
        except (OSError, AttributeError, ValueError):
            pass
        profile = os.environ.get("USERPROFILE")
        if profile:
            candidates.append(Path(profile) / "Documents")
    elif sys.platform != "darwin":
        candidates.append(_xdg_documents(home))
    candidates.append(home / "Documents")
    for candidate in candidates:
        if candidate is not None and _is_folder(candidate):
            return candidate
    return home


def documents_folder() -> Path:
    """The default folder for files (tests replace this function)."""
    return platform_documents_folder()


def expand_home(text: str) -> Path:
    """``text`` as a path, with a leading '~' (alone, or followed by a
    separator) meaning the home folder. '~user' is not expanded."""
    seps = ("/", "\\") if os.name == "nt" else ("/",)
    if text == "~" or any(text.startswith("~" + sep) for sep in seps):
        return Path(os.path.expanduser(text))
    return Path(text)


def resolve_user_path(text: str, base) -> Path:
    """The path a file command means by ``text``.

    A full path is returned as typed; '~' is the home folder. A relative
    path is joined to ``base`` (a folder, or a function returning one, called
    only for a relative path), with '..' resolved as written. Windows paths
    relative to a drive ('D:book.json') or to the current drive's root
    ('\\book.json') are refused, since neither names one file for sure.
    """
    path = expand_home(text)
    if path.is_absolute():
        return path
    if path.drive or path.root:
        raise ValueError(f"{text} is not a full path; give a full path such as "
                         "C:\\Users\\Pat\\book.json.")
    folder = base() if callable(base) else base
    return Path(os.path.normpath(Path(folder) / path))
