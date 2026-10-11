"""Module backups: one ZIP archive holding everything a module contains.

Format "vocab-study-module-backup", version 1
---------------------------------------------
A standard ZIP file, so any unzip tool can open it. Its module/ folder
mirrors the module's own folder::

    vocab-study-backup.json      the manifest (below)
    module/module.json           required
    module/vocab/words.json      the vocabulary
    module/vocab/stats.json      study progress
    module/learning.json         Learning captures, with their source links,
                                 EPUB anchors and review attempts
    module/reading.json          reading positions
    module/notes/<name>.md       Markdown notes
    module/documents/<name>      books and other documents

Nothing else may appear, apart from directory entries for module/,
module/vocab/, module/notes/ and module/documents/. A note or document name
is a single path component: not empty, '.' or '..'; no '/', control
character or invalid UTF-8; not one of the app's temporary names
(.vocab-staging-<32 hex>). On Windows it may also not contain any of
<>:"\\|?*, end with a dot or a space, or be a device name such as CON or
COM1. JSON files and notes are compressed (deflate); documents are stored.

The manifest is a JSON object (UTF-8)::

    {"format": "vocab-study-module-backup", "version": 1,
     "app": "0.2.0", "created": "2026-10-10T15:30:12+00:00",
     "module": "Gatsby",
     "summary": {"words": 42, "notes": 3, "documents": 2, "captures": 15,
                 "reviews": 31},
     "files": [{"path": "module/vocab/words.json", "size": 1234,
                "sha256": "<64 lowercase hex digits>"}, ...]}

"files" lists every member exactly once, with its size in bytes and its
SHA-256. "summary" is informational. Readers ignore keys they do not know.
A version that adds any new kind of content must raise "version", and a
reader refuses versions newer than its own.

The checksums detect damage, not deliberate changes: anyone can rewrite the
manifest too. Restoring therefore also checks every JSON file the way the app
reads it.

Not included: drafts/ (recovery copies of unsaved note changes, which belong
to one installation) and anything else in the module folder that the app
does not manage. Both are reported.

The write lock is held only while the module's small files are read; the
documents are copied afterwards, and each one's size and modification time
are compared before and after. Progress callbacks are never called while the
lock is held, so a desktop progress dialog may process events in them.
"""
from __future__ import annotations

import datetime as _datetime
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ..version import __version__
from .learning import learning_problem
from .module import MODULE_SCHEMA_VERSION, Module
from .storage import _leftover_kind, _publish_file, parse_import, size_text

FORMAT = "vocab-study-module-backup"
VERSION = 1
MANIFEST = "vocab-study-backup.json"

# Members with a fixed name: archive path -> path inside the module folder.
FIXED = {
    "module/module.json": ("module.json",),
    "module/vocab/words.json": ("vocab", "words.json"),
    "module/vocab/stats.json": ("vocab", "stats.json"),
    "module/learning.json": ("learning.json",),
    "module/reading.json": ("reading.json",),
}
MODULE_JSON = "module/module.json"
# Folders whose files are members: archive prefix -> folder inside the module.
FOLDERS = {"module/notes/": "notes", "module/documents/": "documents"}
# Directory entries an archive may hold (some zip tools add them).
DIRECTORIES = {"module/", "module/vocab/", "module/notes/", "module/documents/"}
# Left out on purpose: per-installation recovery copies of unsaved notes.
EXCLUDED = ("drafts",)
# Everything the app manages at the top of a module folder.
MANAGED = {"module.json", "vocab", "notes", "documents", "learning.json",
           "reading.json"} | set(EXCLUDED)

MAX_ENTRIES = 100_000
MAX_JSON_BYTES = 256 << 20      # one JSON file or note
MAX_MANIFEST_BYTES = 32 << 20
SPACE_MARGIN = 64 << 20         # left free on the destination
CHUNK = 1 << 20

# The app's own temporary files: _atomic_write's tempfile names and
# add_document's staging copies. Never reported as unmanaged items.
_TEMP_FILE = re.compile(r"^tmp[a-z0-9_]{8}\.tmp$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_FORBIDDEN = set('<>:"\\|?*')
_WINDOWS_DEVICES = ({"con", "prn", "aux", "nul"}
                    | {f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

Progress = Callable[[int, int, str], None]


class BackupError(ValueError):
    """A backup or restore that could not be done; nothing was written."""


class BackupExists(FileExistsError):
    """The destination exists and replacing it was not confirmed."""

    def __init__(self, path):
        super().__init__(f"{path} already exists.")
        self.path = Path(path)


class Cancelled(Exception):
    """Raised by a progress callback to stop the operation."""


class ArchiveProblem(ValueError):
    """Why a file is not a usable module backup (without the file's name)."""


class ContentProblem(ValueError):
    """A module file whose content the app could not use."""

    def __init__(self, member, reason):
        super().__init__(f"{member}: {reason}")
        self.member = member
        self.reason = str(reason)


def shown(text, limit=100) -> str:
    """Untrusted text (an archive's file names) quoted for a message: repr()
    shows control characters as escapes, and a long name is cut."""
    text = repr(text)
    return text if len(text) <= limit else text[:limit - 4] + "...'"


def relative(member: str) -> str:
    """A member's path inside the module folder: module/vocab/words.json ->
    vocab/words.json."""
    return member[len("module/"):] if member.startswith("module/") else member


def member_name_problem(name: str, windows: Optional[bool] = None) -> Optional[str]:
    """Why ``name`` cannot be a note's or document's file name in a backup
    restored on this computer (or on Windows), or None."""
    windows = os.name == "nt" if windows is None else windows
    if not isinstance(name, str) or name in ("", ".", ".."):
        return "it is not a file name"
    if "/" in name or _CONTROL.search(name):
        return "it contains a slash or a control character"
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return "its name is not valid text (UTF-8)"
    if _leftover_kind(name):
        return "Vocab Study reserves that name for its temporary files"
    if windows:
        if any(ch in _WINDOWS_FORBIDDEN for ch in name):
            return 'it contains one of < > : " \\ | ? *, which Windows does not allow'
        if name[-1] in ". ":
            return "it ends with a dot or a space, which Windows does not allow"
        if name.split(".", 1)[0].rstrip(" ").lower() in _WINDOWS_DEVICES:
            return "it is a device name Windows reserves"
    return None


def member_kind(path: str):
    """("fixed", path) or (folder, file name) for an allowed archive path;
    raises ArchiveProblem for anything else."""
    if path in FIXED:
        return "fixed", path
    for prefix, folder in FOLDERS.items():
        if path.startswith(prefix):
            name = path[len(prefix):]
            problem = member_name_problem(name)
            if problem is None and folder == "notes" and not name.lower().endswith(".md"):
                problem = "a note's name ends with .md"
            if problem:
                raise ArchiveProblem(f"it holds {shown(path)}, which cannot be restored here: "
                                     f"{problem}")
            return folder, name
    raise ArchiveProblem(f"it holds {shown(path)}, which is not part of a module backup")


# ---- content checks (backup and restore) -------------------------------------
def _json(member, data: bytes):
    try:
        return json.loads(data.decode("utf-8-sig"))
    except RecursionError:
        raise ContentProblem(member, "it is nested too deeply to read") from None
    except MemoryError:
        raise ContentProblem(member, "it is too large to read") from None
    except (UnicodeDecodeError, ValueError) as error:
        raise ContentProblem(member, error) from None


def module_problem(raw) -> Optional[str]:
    """Why a module.json cannot be used, or None (its name is not checked:
    the folder name is the module's name)."""
    if not isinstance(raw, dict):
        return "expected a JSON object"
    schema = raw.get("schema", MODULE_SCHEMA_VERSION)
    if (isinstance(schema, bool) or not isinstance(schema, int)
            or not 1 <= schema <= MODULE_SCHEMA_VERSION):
        return f"its format version {schema!r} is not supported by this version of the app"
    components = raw.get("components", [])
    if not isinstance(components, list) or not all(isinstance(c, str) for c in components):
        return "'components' must be a list of names"
    if "created" in raw and not isinstance(raw["created"], str):
        return "'created' must be text"
    if "metadata" in raw and not isinstance(raw["metadata"], dict):
        return "'metadata' must be an object"
    return None


def check_contents(contents: Dict[str, bytes], scheduler) -> dict:
    """Check a module's JSON files, given as {archive path: bytes}, with the
    rules IMPORT and the app's loaders apply. Return counts for the summary;
    raise ContentProblem naming the first unusable file."""
    counts = {"words": 0, "captures": 0, "reviews": 0}
    data = contents.get(MODULE_JSON)
    if data is not None:
        problem = module_problem(_json(MODULE_JSON, data))
        if problem:
            raise ContentProblem(MODULE_JSON, problem)
    member = "module/vocab/words.json"
    if member in contents:
        try:
            words, _ = parse_import(_json(member, contents[member]))
        except ValueError as error:
            if isinstance(error, ContentProblem):
                raise
            raise ContentProblem(member, error) from None
        counts["words"] = len(words.words)
    member = "module/vocab/stats.json"
    if member in contents:
        progress = _json(member, contents[member])
        if not isinstance(progress, dict):
            raise ContentProblem(member, "expected a JSON object")
        for key, card in progress.items():
            problem = scheduler.card_problem(card)
            if problem:
                raise ContentProblem(member, f"progress for {key!r}: {problem}")
    member = "module/learning.json"
    if member in contents:
        learning = _json(member, contents[member])
        problem = learning_problem(learning)
        if problem:
            raise ContentProblem(member, problem)
        counts["captures"] = len(learning["items"])
        counts["reviews"] = sum(len(item["attempts"]) for item in learning["items"].values())
    member = "module/reading.json"
    if member in contents:
        _json(member, contents[member])  # any JSON; a non-object is ignored, as the app does
    return counts


# ---- reading an archive ---------------------------------------------------------
@dataclass
class BackupInfo:
    module: object                 # the manifest's module name (untrusted)
    version: int
    summary: dict
    files: Dict[str, dict]         # archive path -> {"size": int, "sha256": str}
    entries: Dict[str, zipfile.ZipInfo]


def open_archive(path) -> zipfile.ZipFile:
    path = Path(path)
    if path.is_dir():
        raise ArchiveProblem("it is a folder, not a backup file")
    try:
        return zipfile.ZipFile(path)
    except FileNotFoundError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, EOFError,
            NotImplementedError, ValueError):
        raise ArchiveProblem("it is not a ZIP file, or it is damaged") from None


def _damaged(member):
    return ArchiveProblem(f"{shown(member)} is damaged (its contents do not match the "
                          "checksum recorded when the backup was made)")


def stream_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected: dict,
                  sink: Callable[[bytes], None], step: Callable[[int], None] = None) -> None:
    """Pass a member's data to ``sink`` in chunks, stopping at the first byte
    past its declared size, and check its size and SHA-256 against the
    manifest. Damaged data raises ArchiveProblem."""
    declared = info.file_size
    digest = hashlib.sha256()
    count = 0
    try:
        with archive.open(info) as stream:
            while True:
                chunk = stream.read(min(CHUNK, declared - count + 1))
                if not chunk:
                    break
                count += len(chunk)
                if count > declared:  # more data than the archive declares
                    raise _damaged(info.filename)
                digest.update(chunk)
                sink(chunk)
                if step:
                    step(len(chunk))
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as error:
        raise _damaged(info.filename) from error
    if count != expected["size"] or digest.hexdigest() != expected["sha256"]:
        raise _damaged(info.filename)


def read_member(archive, info, expected) -> bytes:
    parts = []
    stream_member(archive, info, expected, parts.append)
    return b"".join(parts)


def inspect_archive(archive: zipfile.ZipFile) -> BackupInfo:
    """Check an open archive's structure and manifest without extracting
    anything. Raises ArchiveProblem."""
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise ArchiveProblem(f"it holds {len(infos):,} files; a backup holds at most "
                             f"{MAX_ENTRIES:,}")
    entries = {}
    for info in infos:
        if info.filename in entries:
            raise ArchiveProblem(f"it holds {shown(info.filename)} twice")
        if info.flag_bits & 0x1:
            raise ArchiveProblem("it is encrypted")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise ArchiveProblem(f"{shown(info.filename)} is a link, which a backup never holds")
        entries[info.filename] = info
    manifest_info = entries.get(MANIFEST)
    if manifest_info is None:
        raise ArchiveProblem(f"it is not a Vocab Study module backup (no {MANIFEST}). A "
                             "vocabulary file from EXPORT is restored with IMPORT")
    if manifest_info.file_size > MAX_MANIFEST_BYTES:
        raise ArchiveProblem(f"its {MANIFEST} is larger than "
                             f"{size_text(MAX_MANIFEST_BYTES)}")
    parts = []
    try:
        with archive.open(manifest_info) as stream:
            while True:
                chunk = stream.read(min(CHUNK, MAX_MANIFEST_BYTES + 1))
                if not chunk:
                    break
                parts.append(chunk)
                if sum(map(len, parts)) > min(manifest_info.file_size, MAX_MANIFEST_BYTES):
                    raise _damaged(MANIFEST)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError) as error:
        raise _damaged(MANIFEST) from error
    try:
        manifest = json.loads(b"".join(parts).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ArchiveProblem(f"its {MANIFEST} is not valid JSON") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise ArchiveProblem(f"its {MANIFEST} does not describe a Vocab Study module backup")
    version = manifest.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ArchiveProblem(f"its backup format version {shown(version)} is not one Vocab "
                             "Study knows")
    if version > VERSION:
        raise ArchiveProblem(f"it was made by a newer version of Vocab Study (backup format "
                             f"{version}; this version reads format {VERSION}). Update Vocab "
                             "Study, then restore it")
    listed = manifest.get("files")
    if not isinstance(listed, list):
        raise ArchiveProblem(f"its {MANIFEST} has no list of files")
    files = {}
    for item in listed:
        path = item.get("path") if isinstance(item, dict) else None
        size = item.get("size") if isinstance(item, dict) else None
        digest = item.get("sha256") if isinstance(item, dict) else None
        if (not isinstance(path, str) or isinstance(size, bool) or not isinstance(size, int)
                or size < 0 or not isinstance(digest, str) or not _SHA256.match(digest)):
            raise ArchiveProblem(f"its {MANIFEST} lists a file without a valid path, size "
                                 "and checksum")
        member_kind(path)
        if path in files:
            raise ArchiveProblem(f"its {MANIFEST} lists {shown(path)} twice")
        files[path] = {"size": size, "sha256": digest}
    for name, info in entries.items():
        if name == MANIFEST:
            continue
        if name in DIRECTORIES and info.is_dir() and info.file_size == 0:
            continue
        if name not in files:
            member_kind(name)  # names a path outside the format first
            raise ArchiveProblem(f"it holds {shown(name)}, which its {MANIFEST} does not list")
    for path, expected in files.items():
        info = entries.get(path)
        if info is None or info.is_dir():
            raise ArchiveProblem(f"{shown(path)} is missing from it")
        if info.file_size != expected["size"]:
            raise _damaged(path)
        if path in FIXED and info.file_size > MAX_JSON_BYTES:
            raise ArchiveProblem(f"{shown(path)} is larger than {size_text(MAX_JSON_BYTES)}, "
                                 "the most a backup holds for one such file")
    if MODULE_JSON not in files:
        raise ArchiveProblem(f"{MODULE_JSON} is missing from it")
    summary = manifest.get("summary")
    return BackupInfo(module=manifest.get("module"), version=version,
                      summary=summary if isinstance(summary, dict) else {},
                      files=files, entries=entries)


def read_backup(path) -> BackupInfo:
    """Check a backup file's structure and manifest, extracting nothing."""
    with open_archive(path) as archive:
        return inspect_archive(archive)


# ---- writing a backup -----------------------------------------------------------
@dataclass
class _Document:
    member: str
    path: Path
    size: int
    mtime_ns: int


@dataclass
class Snapshot:
    name: str
    folder: Path
    contents: Dict[str, bytes]          # JSON files and notes, by archive path
    mtimes: Dict[str, float]
    documents: List[_Document]
    drafts: List[str]
    others: List[str]                   # unmanaged items, relative to the folder


@dataclass
class BackupResult:
    path: Path
    module: str
    summary: dict
    size: int
    folder: Path
    drafts: List[str] = field(default_factory=list)
    others: List[str] = field(default_factory=list)


def _today():
    return _datetime.date.today()


def default_backup_path(folder, module: str, today=None) -> Path:
    """<folder>/<module> <YYYY-MM-DD>.zip, or ' (2)', ' (3)'... when taken."""
    stem = f"{module} {(today or _today()):%Y-%m-%d}"
    folder = Path(folder)
    candidate = folder / f"{stem}.zip"
    number = 2
    while os.path.lexists(candidate):
        candidate = folder / f"{stem} ({number}).zip"
        number += 1
    return candidate


def _refuse(name, reason):
    raise BackupError(f"Could not back up '{name}': {reason}. Nothing was written.")


def _reason(error: OSError) -> str:
    """An OSError as message text: its reason and the file it names."""
    if error.strerror:
        return error.strerror + (f": {error.filename}" if error.filename else "")
    return str(error).rstrip(".") or type(error).__name__


def _read_small(name, path: Path):
    """A JSON file's or note's bytes and modification time, or None when it
    does not exist."""
    try:
        with open(path, "rb") as stream:
            st = os.fstat(stream.fileno())
            if st.st_size > MAX_JSON_BYTES:
                _refuse(name, f"{path} is larger than {size_text(MAX_JSON_BYTES)}, the most a "
                              "backup holds for one such file")
            return stream.read(MAX_JSON_BYTES + 1), st.st_mtime
    except FileNotFoundError:
        return None
    except OSError as error:
        _refuse(name, f"Could not read {path}: {error.strerror or error}")


def _entries(name, folder: Path):
    try:
        return sorted(folder.iterdir()) if folder.is_dir() else []
    except OSError as error:
        _refuse(name, f"Could not read {folder}: {error.strerror or error}")


def _name_check(name, path: Path):
    problem = member_name_problem(path.name)
    if problem:
        _refuse(name, f"{ascii(path.name)} in {path.parent} cannot be restored on this computer "
                      f"({problem}). Rename it, then back up again")


def snapshot(storage, name: str) -> Snapshot:
    """Read the module's small files and list its documents, under the write
    lock, without changing anything (load_module and module_identity would
    write a missing module.json)."""
    with storage.transaction():
        name = storage.canonical_name(name)
        folder = storage.paths.module_dir(name)
        contents, mtimes = {}, {}
        for member, parts in FIXED.items():
            found = _read_small(name, folder.joinpath(*parts))
            if found is not None:
                contents[member], mtimes[member] = found
        if MODULE_JSON not in contents:
            # A legacy module: the archive gets a manifest, the module does not.
            contents[MODULE_JSON] = json.dumps(Module(name=name).to_dict(), ensure_ascii=False,
                                               indent=2).encode("utf-8")
            mtimes[MODULE_JSON] = time.time()
        others, documents = [], []
        for entry in _entries(name, folder):
            if entry.name not in MANAGED and not _TEMP_FILE.match(entry.name):
                others.append(entry.name)
        for entry in _entries(name, folder / "vocab"):
            if entry.name not in ("words.json", "stats.json") and not _TEMP_FILE.match(entry.name):
                others.append(f"vocab/{entry.name}")
        for entry in _entries(name, folder / "notes"):
            if entry.name.lower().endswith(".md") and entry.is_file():
                _name_check(name, entry)
                member = "module/notes/" + entry.name
                contents[member], mtimes[member] = _read_small(name, entry) or (b"", time.time())
            elif not _TEMP_FILE.match(entry.name):
                others.append(f"notes/{entry.name}")
        for entry in _entries(name, folder / "documents"):
            if _leftover_kind(entry.name):
                continue  # an unfinished add_document copy
            try:
                if not entry.is_file():
                    others.append(f"documents/{entry.name}")
                    continue
                _name_check(name, entry)
                st = entry.stat()
            except OSError as error:
                _refuse(name, f"Could not read {entry}: {error.strerror or error}")
            documents.append(_Document("module/documents/" + entry.name, entry, st.st_size,
                                       st.st_mtime_ns))
        drafts = storage.note_drafts(name)
    return Snapshot(name, folder, contents, mtimes, documents, drafts, others)


def _date_time(mtime: float):
    """A zip timestamp (local time) for a modification time, kept inside the
    years a zip file can store."""
    try:
        stamp = time.localtime(mtime)[:6]
    except (OverflowError, OSError, ValueError):
        return (1980, 1, 1, 0, 0, 0)
    if stamp[0] < 1980:
        return (1980, 1, 1, 0, 0, 0)
    if stamp[0] > 2107:
        return (2107, 12, 31, 23, 59, 58)
    return stamp


def _zip_info(member: str, mtime: float, compress: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(member, _date_time(mtime))
    info.compress_type = compress
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _write_chunk(stream, data: bytes) -> None:
    """One chunk of a document into the archive (tests replace it)."""
    stream.write(data)


def _refuse_inside(name, dest: Path, modules_dir: Path):
    parent = os.path.normcase(os.path.realpath(dest.parent))
    modules = os.path.normcase(os.path.realpath(modules_dir))
    if parent == modules or parent.startswith(modules.rstrip(os.sep) + os.sep):
        _refuse(name, f"{dest} is inside Vocab Study's modules folder {modules_dir}. Choose "
                      "a destination outside it")


def _landed(tmp: str, path: Path, size: int) -> bool:
    """Whether the temporary archive is already in place at ``path`` (an
    interrupt can arrive after the link or rename, before it is noted)."""
    try:
        if os.path.lexists(tmp):
            return os.path.lexists(path) and os.path.samefile(tmp, path)
        return os.path.getsize(path) == size  # renamed into place
    except OSError:
        return False


def _build(raw, snap: Snapshot, summary, total, label, progress, now) -> dict:
    """Write the archive into the open temporary file; return its manifest."""
    archive = zipfile.ZipFile(raw, "w", allowZip64=True)
    try:
        files = []
        done = 0
        progress(0, total, label)
        for member in sorted(snap.contents, key=lambda m: (m not in FIXED, m)):
            data = snap.contents[member]
            archive.writestr(_zip_info(member, snap.mtimes[member], zipfile.ZIP_DEFLATED), data)
            files.append({"path": member, "size": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
            done += len(data)
        progress(done, total, label)
        for document in snap.documents:
            files.append(_copy_document(archive, snap.name, document, done, total, label,
                                        progress))
            done += document.size
        manifest = {"format": FORMAT, "version": VERSION, "app": __version__,
                    "created": (now or _datetime.datetime.now(_datetime.timezone.utc))
                    .astimezone(_datetime.timezone.utc).replace(microsecond=0).isoformat(),
                    "module": snap.name, "summary": summary, "files": files}
        archive.writestr(_zip_info(MANIFEST, time.time(), zipfile.ZIP_DEFLATED),
                         json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    except BaseException:
        archive.fp = None  # abandoned: the temporary file is removed, not finished
        raise
    archive.close()
    return manifest


def write_backup(storage, name: str, dest, *, scheduler, replace: bool = False,
                 progress: Optional[Progress] = None, auto_number: bool = False,
                 now: Optional[_datetime.datetime] = None) -> BackupResult:
    """Write a module's backup to ``dest`` (a .zip path whose folder exists).

    An existing ``dest`` is replaced only with ``replace``; otherwise
    BackupExists is raised before anything is written. With ``auto_number``
    ``dest`` is a default dated name, and a name taken meanwhile moves on to
    the next free one. The archive is built in a temporary file beside
    ``dest``, read back and checked, and only then published. A failure,
    Cancelled or KeyboardInterrupt leaves no file behind; once the archive
    is published, the result is returned even if an interrupt arrives.
    """
    dest = Path(dest)
    progress = progress or (lambda done, total, label: None)
    _refuse_inside(name, dest, storage.paths.modules_dir)
    try:
        snap = snapshot(storage, name)
    except (BackupError, BackupExists):
        raise
    except OSError as error:  # an unreadable folder, or another process holding the lock
        _refuse(name, _reason(error))
    name = snap.name
    if not dest.parent.is_dir():
        raise BackupError(f"Could not write {dest}: the folder {dest.parent} does not exist. "
                          "Nothing was written.")
    next_free = (lambda: default_backup_path(dest.parent, name)) if auto_number else None
    if os.path.lexists(dest) and not replace:
        if next_free is None:
            raise BackupExists(dest)
        dest = next_free()
    # Checked outside the lock, on exactly the bytes the archive will hold.
    try:
        counts = check_contents(snap.contents, scheduler)
    except ContentProblem as problem:
        raise BackupError(
            f"Could not back up '{name}': Could not read "
            f"{snap.folder.joinpath(*relative(problem.member).split('/'))}: {problem.reason}. "
            f"Nothing was written. Repair or move that file, or copy the folder "
            f"{snap.folder} yourself.") from None
    if len(snap.contents) + len(snap.documents) + 1 > MAX_ENTRIES:
        _refuse(name, f"it has more than {MAX_ENTRIES:,} files, the most a backup holds")
    summary = {"words": counts["words"],
               "notes": sum(1 for m in snap.contents if m.startswith("module/notes/")),
               "documents": len(snap.documents), "captures": counts["captures"],
               "reviews": counts["reviews"]}
    total = sum(map(len, snap.contents.values())) + sum(d.size for d in snap.documents)
    try:
        free = shutil.disk_usage(dest.parent).free
    except OSError:
        free = None
    if free is not None and free < total + SPACE_MARGIN:
        raise BackupError(f"Could not write {dest}: the backup needs about {size_text(total)} "
                          f"and {dest.parent} has {size_text(free)} free. Nothing was written.")
    label = f"Backing up '{name}'"
    published = False
    result = None
    try:
        fd, tmp = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".partial", dir=dest.parent)
        try:
            with open(fd, "w+b") as raw:
                manifest = _build(raw, snap, summary, total, label, progress, now)
                raw.flush()
                os.fsync(raw.fileno())
            _verify(tmp, manifest, snap.contents)
            result = BackupResult(dest, name, summary, os.path.getsize(tmp), snap.folder,
                                  snap.drafts, snap.others)
            # No progress call from here on: the dialog's Cancel no longer applies.
            while True:
                try:
                    if replace:
                        os.replace(tmp, result.path)
                    else:  # never replaces a file that appeared meanwhile
                        _publish_file(Path(tmp), result.path, f"{result.path} already exists.")
                    break
                except FileExistsError:
                    if next_free is None:
                        raise BackupExists(result.path) from None
                    # A dated name taken meanwhile: never replace a file the
                    # user did not name; take the next free one.
                    result.path = next_free()
            published = True
        finally:
            if result is not None and not published:
                published = _landed(tmp, result.path, result.size)
            try:
                os.unlink(tmp)
            except OSError:
                pass  # published by rename, or held a moment by an antivirus (it is hidden)
    except (KeyboardInterrupt, Cancelled):
        if published:
            return result  # complete and in place: report it, not a cancel
        raise
    except (BackupError, BackupExists):
        raise
    except OSError as error:
        if published:
            return result
        raise BackupError(f"Could not write {dest}: {error.strerror or error}. Nothing was "
                          "written.") from error
    return result


def _copy_document(archive, name, document: _Document, done, total, label, progress) -> dict:
    """Stream one document into the archive; refuse one that changes or
    disappears meanwhile."""
    changed = (f"Could not back up '{name}': the document '{document.path.name}' changed while "
               "it was being copied. Nothing was written; try again.")
    digest = hashlib.sha256()
    count = 0
    info = _zip_info(document.member, document.mtime_ns / 1e9, zipfile.ZIP_STORED)
    info.file_size = document.size  # Zip64 only for documents that need it
    try:
        with open(document.path, "rb") as source, archive.open(info, "w") as target:
            while True:
                chunk = source.read(CHUNK)
                if not chunk:
                    break
                count += len(chunk)
                digest.update(chunk)
                _write_chunk(target, chunk)
                progress(done + min(count, document.size), total, label)
    except FileNotFoundError:
        raise BackupError(f"Could not back up '{name}': the document '{document.path.name}' "
                          "was removed while it was being copied. Nothing was written; try "
                          "again.") from None
    except RuntimeError:  # it grew past the size zipfile was told
        raise BackupError(changed) from None
    try:
        st = os.stat(document.path)
    except OSError:
        raise BackupError(changed) from None
    if count != document.size or (st.st_size, st.st_mtime_ns) != (document.size,
                                                                   document.mtime_ns):
        raise BackupError(changed)
    return {"path": document.member, "size": count, "sha256": digest.hexdigest()}


def _verify(tmp, manifest, contents) -> None:
    """Read the finished archive back: its structure and manifest, as RESTORE
    reads them, and the checksums of the JSON files and notes. Documents
    were hashed as they went in, and the zip CRC covers them."""
    try:
        with open_archive(tmp) as archive:
            info = inspect_archive(archive)
            if info.files != {f["path"]: {"size": f["size"], "sha256": f["sha256"]}
                              for f in manifest["files"]}:
                raise ArchiveProblem("its file list differs from the one written")
            for member in contents:
                read_member(archive, info.entries[member], info.files[member])
    except ArchiveProblem as problem:
        raise OSError(f"the archive written could not be read back ({problem})") from None
