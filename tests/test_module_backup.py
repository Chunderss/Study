"""Module backups: BACKUP writes one archive holding a whole module.

Core and command tests without Qt. Archives go to the folder for files,
which the conftest points at a temporary Documents folder.
"""
import errno
import hashlib
import io
import json
import os
import re
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from vocab.cli.app import App
from vocab.cli.parser import parse
from vocab.cli.repl import Repl
from vocab.core import backup
from vocab.core.learning import LearningStore
from vocab.core.locking import write_lock

DATE = "2026-10-10"
EPUB_PAGE = ('<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title></head>'
             '<body>{1}</body></html>')


@pytest.fixture(autouse=True)
def frozen_date(monkeypatch):
    import datetime
    monkeypatch.setattr(backup, "_today", lambda: datetime.date(2026, 10, 10))


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Repl, "_startup_checks", lambda self: None)
    return App(tmp_path / "home")


def run(app, line, confirm=None):
    repl = Repl(app, in_stream=io.StringIO(), out_stream=io.StringIO())
    if confirm is not None:
        repl._confirm = confirm
    try:
        return repl.dispatch(parse(line))
    except Exception as error:  # what the loop prints
        return f"! {error}"


def write_epub(path, chapters=(("One", "Passage for reading."), ("Two", "Second part."))):
    with zipfile.ZipFile(path, "w") as book:
        items = "".join(f'<item id="c{i}" href="c{i}.xhtml"/>' for i in range(len(chapters)))
        refs = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
        book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf">'
                      f"<manifest>{items}</manifest><spine>{refs}</spine></package>")
        for i, (title, text) in enumerate(chapters):
            book.writestr(f"c{i}.xhtml", EPUB_PAGE.format(title, f"<p>{text}</p>" * 40))


def make_module(app, tmp_path, name="Gatsby"):
    """A module with every kind of study data the app keeps."""
    app.cmd_create(name)
    app.cmd_use(name)
    app.cmd_add("ephemeral", manual_def="lasting a very short time")
    app.cmd_add("laconic", manual_def="using very few words")
    stats = app.storage.load_stats(name)
    stats["ephemeral"].update(box=3, reps=4, due=1_800_000_000.0, last=1_700_000_000.0)
    app.storage.save_stats(name, stats)
    notes = app.paths.notes_dir(name)
    (notes / "Ch1.md").write_bytes("﻿Café notes\r\nline two\r\n".encode("utf-8"))
    (notes / "Double.md.md").write_text("a legacy name", encoding="utf-8")
    (notes / "Café.md").write_text("written directly", encoding="utf-8")
    source = tmp_path / "paper.pdf"
    source.write_bytes(bytes(range(256)) * 64)
    app.storage.add_document(name, source)
    write_epub(tmp_path / "book.epub")
    app.storage.add_document(name, tmp_path / "book.epub")
    learning = LearningStore(app.storage)
    learning.save_capture(name, kind="question", prompt="Why paper?", quote="byte values",
                          source={"kind": "pdf", "filename": "paper.pdf", "page": 1})
    concept = learning.save_capture(
        name, kind="concept", prompt="What is read?", quote="Passage for reading.",
        source={"kind": "epub", "filename": "book.epub", "chapter": 0, "scroll": 0.25,
                "anchor": {"exact": "Passage for reading.", "prefix": "", "suffix": ""}})
    learning.review(name, concept["id"], concept["revision"], "the passage", "good",
                    now=1_700_000_000.0)
    app.storage.save_reading_pos(name, "book.epub", {"chapter": 1, "scroll": 0.5, "zoom": 1.2})
    app.storage.save_reading_pos(name, "paper.pdf", {"page": 2})
    return app.paths.module_dir(name)


def tree(folder: Path):
    """Every file's bytes and every folder, relative to ``folder``."""
    files, folders = {}, set()
    for root, dirs, names in os.walk(folder):
        for d in dirs:
            folders.add(os.path.relpath(os.path.join(root, d), folder))
        for n in names:
            path = os.path.join(root, n)
            files[os.path.relpath(path, folder)] = Path(path).read_bytes()
    return files, folders


def leftovers(folder: Path):
    return [p.name for p in folder.iterdir() if p.name.endswith(".partial")]


def members(path):
    with zipfile.ZipFile(path) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


# ---- what an archive holds -------------------------------------------------------
def test_backup_archive_holds_every_kind_of_study_data(app, tmp_path, documents_folder):
    folder = make_module(app, tmp_path)
    reply = run(app, "BACKUP Gatsby")
    dest = documents_folder / f"Gatsby {DATE}.zip"
    assert reply.startswith(f"Backed up 'Gatsby' -> {dest} (2 words, 3 notes, 2 documents, "
                            "2 captures; ")
    assert dest.is_file()
    content = members(dest)
    expected = {
        "module/module.json": folder / "module.json",
        "module/vocab/words.json": folder / "vocab" / "words.json",
        "module/vocab/stats.json": folder / "vocab" / "stats.json",
        "module/learning.json": folder / "learning.json",
        "module/reading.json": folder / "reading.json",
        "module/notes/Ch1.md": folder / "notes" / "Ch1.md",
        "module/notes/Double.md.md": folder / "notes" / "Double.md.md",
        "module/notes/Café.md": folder / "notes" / "Café.md",
        "module/documents/paper.pdf": folder / "documents" / "paper.pdf",
        "module/documents/book.epub": folder / "documents" / "book.epub",
    }
    assert set(content) == set(expected) | {backup.MANIFEST}
    for member, path in expected.items():
        assert content[member] == path.read_bytes(), member
    manifest = json.loads(content[backup.MANIFEST])
    assert manifest["format"] == "vocab-study-module-backup" and manifest["version"] == 1
    assert manifest["module"] == "Gatsby" and manifest["app"]
    assert manifest["created"].endswith("+00:00")
    assert manifest["summary"] == {"words": 2, "notes": 3, "documents": 2, "captures": 2,
                                   "reviews": 1}
    listed = {f["path"]: f for f in manifest["files"]}
    assert set(listed) == set(expected)
    for member, path in expected.items():
        data = path.read_bytes()
        assert listed[member]["size"] == len(data)
        assert listed[member]["sha256"] == hashlib.sha256(data).hexdigest()
    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None
        kinds = {info.filename: info.compress_type for info in archive.infolist()}
    assert kinds["module/documents/paper.pdf"] == zipfile.ZIP_STORED
    assert kinds["module/vocab/words.json"] == zipfile.ZIP_DEFLATED
    info = backup.read_backup(dest)  # the reader RESTORE uses accepts it
    assert info.module == "Gatsby" and info.version == 1


def test_backup_leaves_the_module_exactly_as_it_was(app, tmp_path, documents_folder):
    folder = make_module(app, tmp_path)
    before = tree(app.paths.root)
    run(app, "BACKUP Gatsby")
    assert tree(app.paths.root) == before
    # A legacy module without module.json gets one in the archive only.
    app.cmd_create("Legacy")
    (app.paths.module_dir("Legacy") / "module.json").unlink()
    before = tree(app.paths.module_dir("Legacy"))
    assert run(app, "BACKUP Legacy").startswith("Backed up 'Legacy'")
    assert tree(app.paths.module_dir("Legacy")) == before
    manifest = json.loads(members(documents_folder / f"Legacy {DATE}.zip")["module/module.json"])
    assert manifest["name"] == "Legacy" and manifest["schema"] == 1


def test_backup_default_names_folders_and_suffix(app, tmp_path, documents_folder):
    make_module(app, tmp_path, "Book")
    assert f"-> {documents_folder / f'Book {DATE}.zip'} (" in run(app, "BACKUP Book")
    assert f"-> {documents_folder / f'Book {DATE} (2).zip'} (" in run(app, "BACKUP Book")
    shelf = tmp_path / "Backup shelf"
    shelf.mkdir()
    assert f"-> {shelf / f'Book {DATE}.zip'} (" in run(app, f'BACKUP Book "{shelf}"')
    assert f"-> {documents_folder / 'name.zip'} (" in run(app, "BACKUP Book name")
    assert f"-> {documents_folder / 'Other.ZIP'} (" in run(app, "BACKUP Book Other.ZIP")
    missing = run(app, "BACKUP Book sub/x.zip")
    assert missing == (f"! Could not write {documents_folder / 'sub' / 'x.zip'}: the folder "
                       f"{documents_folder / 'sub'} does not exist. Nothing was written.")
    assert not (documents_folder / "sub").exists()  # BACKUP never creates folders
    assert run(app, "BACKUP Nope") == "! Module 'Nope' does not exist."


def test_backup_replaces_an_existing_file_only_after_confirmation(app, tmp_path,
                                                                  documents_folder):
    make_module(app, tmp_path)
    old = documents_folder / "old.zip"
    old.write_bytes(b"an older backup")
    asked = []
    reply = run(app, "BACKUP Gatsby old.zip", confirm=lambda text: asked.append(text) or False)
    assert reply == "Cancelled; nothing was written."
    assert asked == [f"{old} already exists. Replace it?"]
    assert old.read_bytes() == b"an older backup"
    reply = run(app, "BACKUP Gatsby old", confirm=lambda text: True)  # .zip appended
    assert reply.startswith(f"Backed up 'Gatsby' -> {old} (")
    assert backup.read_backup(old).module == "Gatsby"
    assert leftovers(documents_folder) == []
    # The terminal asks on its own input.
    old.write_bytes(b"older again")
    out = io.StringIO()
    repl = Repl(app, in_stream=io.StringIO("n\n"), out_stream=out)
    assert repl.dispatch(parse("BACKUP Gatsby old.zip")) == "Cancelled; nothing was written."
    assert f"{old} already exists. Replace it? [y/N]: " in out.getvalue()
    assert old.read_bytes() == b"older again"


DAMAGE = {
    "words": ("vocab/words.json", lambda raw: raw["words"]["ephemeral"]["senses"][0].update(
        definition=" "), "a definition is empty"),
    "stats": ("vocab/stats.json", lambda raw: raw["ephemeral"].update(due="tomorrow"),
              "'due' must be a time in seconds"),
    "learning": ("learning.json", lambda raw: next(iter(raw["items"].values())).pop("resolved"),
                 "'resolved' must be true or false"),
    "reading": ("reading.json", "{bad", "Expecting property name"),
    "module": ("module.json", "[]", "expected a JSON object"),
}


@pytest.mark.parametrize("kind", sorted(DAMAGE))
def test_backup_refuses_a_damaged_module_and_names_the_file(app, tmp_path, documents_folder,
                                                            kind):
    folder = make_module(app, tmp_path)
    relative, change, reason = DAMAGE[kind]
    path = folder / relative
    if isinstance(change, str):
        path.write_text(change, encoding="utf-8")
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        change(raw)
        path.write_text(json.dumps(raw), encoding="utf-8")
    damaged = path.read_bytes()
    reply = run(app, "BACKUP Gatsby")
    assert reply.startswith(f"! Could not back up 'Gatsby': Could not read {path}: "), reply
    assert reason in reply
    assert f"Nothing was written. Repair or move that file, or copy the folder {folder} " \
           "yourself." in reply
    assert list(documents_folder.iterdir()) == []
    assert path.read_bytes() == damaged


def test_failed_backup_leaves_nothing_and_a_retry_succeeds(app, tmp_path, documents_folder,
                                                           monkeypatch):
    make_module(app, tmp_path)
    monkeypatch.setattr(backup, "CHUNK", 1024)
    streams = []
    real = backup._write_chunk

    def full_disk(stream, data):
        if stream not in streams:
            streams.append(stream)
        if len(streams) == 2:  # the second document
            raise OSError(errno.ENOSPC, "No space left on device")
        real(stream, data)
    monkeypatch.setattr(backup, "_write_chunk", full_disk)
    dest = documents_folder / f"Gatsby {DATE}.zip"
    assert run(app, "BACKUP Gatsby") == \
        f"! Could not write {dest}: No space left on device. Nothing was written."
    assert list(documents_folder.iterdir()) == []
    monkeypatch.setattr(backup, "_write_chunk", real)
    assert run(app, "BACKUP Gatsby").startswith(f"Backed up 'Gatsby' -> {dest} (")
    assert backup.read_backup(dest).summary["documents"] == 2
    with zipfile.ZipFile(dest) as archive:
        assert archive.testzip() is None


def test_backup_refuses_a_document_that_changes_while_copied(app, tmp_path, documents_folder,
                                                             monkeypatch):
    folder = make_module(app, tmp_path)
    monkeypatch.setattr(backup, "CHUNK", 1024)
    paper = folder / "documents" / "paper.pdf"
    seen = []

    def appending(done, total, label):
        seen.append(done)
        if len(seen) == 4:  # inside the first document
            with open(paper, "ab") as stream:
                stream.write(b"a new page")
    with pytest.raises(backup.BackupError, match="changed while it was being copied. Nothing "
                                                 "was written; try again."):
        backup.write_backup(app.storage, "Gatsby", documents_folder / "x.zip",
                            scheduler=app.scheduler, progress=appending)
    assert list(documents_folder.iterdir()) == []


def deny(monkeypatch, path):
    """Make opening ``path`` fail as an unreadable or locked file does, for
    open() and for zipfile, which calls io.open."""
    real_open = open

    def guarded(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and os.fspath(file) == os.fspath(path):
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(path))
        return real_open(file, *args, **kwargs)
    monkeypatch.setattr("builtins.open", guarded)
    monkeypatch.setattr(io, "open", guarded)


def test_backup_names_a_document_it_cannot_read(app, tmp_path, documents_folder, monkeypatch):
    folder = make_module(app, tmp_path)
    paper = folder / "documents" / "paper.pdf"
    with monkeypatch.context() as patch:
        deny(patch, paper)  # no permission, or opened without sharing by another program
        assert run(app, "BACKUP Gatsby") == (f"! Could not back up 'Gatsby': Could not read "
                                             f"{paper}: Permission denied. Nothing was written.")
        assert run(app, "BACKUPALL").endswith(
            f"! Not backed up: Gatsby: Could not back up 'Gatsby': Could not read {paper}: "
            "Permission denied. Nothing was written.")
    assert list(documents_folder.iterdir()) == []
    # A read error part way through names the document too.
    real_open = open

    class FailingRead:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            raise OSError(errno.EIO, "Input/output error")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()

    def failing(file, *args, **kwargs):
        stream = real_open(file, *args, **kwargs)
        is_paper = isinstance(file, (str, os.PathLike)) and os.fspath(file) == os.fspath(paper)
        return FailingRead(stream) if is_paper else stream
    with monkeypatch.context() as patch:
        patch.setattr("builtins.open", failing)
        assert run(app, "BACKUP Gatsby") == (f"! Could not back up 'Gatsby': Could not read "
                                             f"{paper}: Input/output error. Nothing was written.")
    assert list(documents_folder.iterdir()) == []
    assert run(app, "BACKUP Gatsby").startswith("Backed up 'Gatsby'")


@pytest.mark.parametrize("stop", [backup.Cancelled, KeyboardInterrupt])
def test_cancelled_backup_writes_nothing(app, tmp_path, documents_folder, stop):
    make_module(app, tmp_path)

    def cancel(done, total, label):
        if done:
            raise stop()
    assert app.cmd_backup("Gatsby", progress=cancel) == "Backup cancelled; nothing was written."
    assert list(documents_folder.iterdir()) == []
    assert app.cmd_backup("Gatsby").startswith("Backed up 'Gatsby'")


def test_an_interrupt_after_publishing_reports_the_backup(app, tmp_path, documents_folder,
                                                          monkeypatch):
    make_module(app, tmp_path)
    real = backup._publish_file

    def published_then_interrupted(*args):
        real(*args)
        raise KeyboardInterrupt
    monkeypatch.setattr(backup, "_publish_file", published_then_interrupted)
    dest = documents_folder / f"Gatsby {DATE}.zip"
    assert app.cmd_backup("Gatsby").startswith(f"Backed up 'Gatsby' -> {dest} (")
    assert backup.read_backup(dest).module == "Gatsby"
    assert leftovers(documents_folder) == []


def test_backup_mentions_drafts_and_unmanaged_items(app, tmp_path, documents_folder):
    folder = make_module(app, tmp_path)
    app.storage.save_note_draft("Gatsby", "Ch1", "Café notes", "unsaved text")
    app.storage.save_note_draft("Gatsby", "Double.md", "a legacy name", "more")
    (folder / "my-own-file.txt").write_text("mine", encoding="utf-8")
    (folder / "documents" / "scans").mkdir()
    # The app's own temporary files are neither archived nor reported.
    (folder / "vocab" / "tmpabcd1234.tmp").write_text("half", encoding="utf-8")
    (folder / "documents" / f".vocab-staging-{'0' * 32}").write_text("half", encoding="utf-8")
    reply = run(app, "BACKUP Gatsby")
    lines = reply.splitlines()
    assert lines[1] == ("  Not included: recovery copies of unsaved changes to the notes 'Ch1', "
                        "'Double.md'. Save them in the desktop app, then back up again.")
    assert lines[2] == (f"  Not included: 2 other items in {folder} that Vocab Study does not "
                        "manage.")
    content = members(documents_folder / f"Gatsby {DATE}.zip")
    assert not any("drafts" in m or "my-own" in m or "scans" in m or "tmp" in m
                   or "staging" in m for m in content)


def test_backup_usage_and_quote_errors(app, tmp_path, documents_folder):
    make_module(app, tmp_path)
    usage = ("Usage: BACKUP <module> [<file.zip> | <folder>]   (quote a name or path that "
             "contains spaces)")
    assert run(app, "BACKUP") == usage
    assert run(app, "BACKUP Gatsby a.zip b.zip") == usage
    assert "cannot start or end with a quote" in run(app, "BACKUP Gatsby 'a.zip'")
    assert list(documents_folder.iterdir()) == []
    app.cmd_create("The Great Gatsby")
    reply = run(app, 'BACKUP "The Great Gatsby" "Great backup.zip"')
    assert reply.startswith(f"Backed up 'The Great Gatsby' -> {documents_folder / 'Great backup.zip'}")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file names")
@pytest.mark.parametrize("raw_name", [b"bad\x01name.pdf", b"caf\xe9.pdf"])
def test_backup_refuses_an_unrestorable_name(app, tmp_path, documents_folder, raw_name):
    folder = make_module(app, tmp_path)
    with open(os.path.join(os.fsencode(folder / "documents"), raw_name), "wb") as stream:
        stream.write(b"%PDF")
    reply = run(app, "BACKUP Gatsby")
    assert reply.startswith("! Could not back up 'Gatsby': ")
    assert "cannot be restored on this computer" in reply and "Rename it" in reply
    assert ascii(os.fsdecode(raw_name)) in reply
    assert list(documents_folder.iterdir()) == []


def test_backup_destination_inside_the_modules_folder_is_refused(app, tmp_path):
    folder = make_module(app, tmp_path)
    before = tree(app.paths.root)
    for dest in (folder / "documents" / "self.zip", app.paths.modules_dir / "x.zip"):
        reply = run(app, f'BACKUP Gatsby "{dest}"')
        assert reply.startswith("! Could not back up 'Gatsby': ")
        assert "is inside Vocab Study's modules folder" in reply
    assert tree(app.paths.root) == before


def test_progress_is_never_called_while_the_write_lock_is_held(app, tmp_path, documents_folder,
                                                                 monkeypatch):
    # A desktop progress dialog processes events in these calls; holding the
    # lock there would stall every other window's saves.
    make_module(app, tmp_path)
    monkeypatch.setattr(backup, "CHUNK", 4096)
    lock = write_lock(app.paths.root)
    calls = []

    def check(done, total, label):
        assert lock._depth == 0
        acquired = []

        def other():
            try:
                with lock.hold(timeout=0):
                    acquired.append(True)
            except OSError:
                acquired.append(False)
        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        assert acquired == [True]
        calls.append((done, total, label))
    reply = app.cmd_backup("Gatsby", progress=check)
    assert reply.startswith("Backed up")
    assert len(calls) > 3 and calls[-1][0] == calls[-1][1]
    assert {label for _, _, label in calls} == {"Backing up 'Gatsby'"}


def test_large_backups_announce_themselves_in_the_terminal(app, tmp_path, monkeypatch):
    make_module(app, tmp_path)
    import vocab.cli.repl as repl_module
    monkeypatch.setattr(repl_module, "PROGRESS_THRESHOLD", 1000)
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO(), out_stream=out).dispatch(parse("BACKUP Gatsby"))
    assert out.getvalue().startswith("Backing up 'Gatsby' (")
    assert out.getvalue().rstrip().endswith("... Ctrl-C cancels.")
    monkeypatch.setattr(repl_module, "PROGRESS_THRESHOLD", 1 << 40)
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO(), out_stream=out).dispatch(parse("BACKUP Gatsby"))
    assert out.getvalue() == ""


def test_backup_covers_every_file_the_app_writes(app, tmp_path, documents_folder):
    # Every file a module-writing feature creates is either in the archive or
    # one of the documented exclusions (drafts/). A new per-module file must
    # be added to the backup format, or this fails.
    folder = make_module(app, tmp_path)
    app.cmd_note_add("Fresh", "text")
    app.storage.create_note("Gatsby", "Empty")
    app.storage.save_note_draft("Gatsby", "Fresh", "text", "draft")
    app.storage.module_identity("Gatsby")
    app.cmd_delete_word("laconic")
    run(app, "BACKUP Gatsby")
    archived = {m[len("module/"):] for m in members(documents_folder / f"Gatsby {DATE}.zip")
                if m.startswith("module/")}
    files, _ = tree(folder)
    for path in files:
        path = path.replace(os.sep, "/")
        assert path in archived or path.split("/")[0] in backup.EXCLUDED, path


# ---- RESTORE ----------------------------------------------------------------------
def sha(data):
    return hashlib.sha256(data).hexdigest()


def made_backup(app, tmp_path, documents_folder, name="Gatsby"):
    make_module(app, tmp_path, name)
    assert run(app, f"BACKUP {name}").startswith("Backed up")
    return documents_folder / f"{name} {DATE}.zip"


def rewrite(src, dest, edit=None, recompute=True):
    """A copy of the archive ``src`` with ``edit(files, manifest)`` applied;
    with ``recompute`` the manifest's file list matches the new files."""
    with zipfile.ZipFile(src) as archive:
        files = {info.filename: archive.read(info) for info in archive.infolist()}
    manifest = json.loads(files.pop(backup.MANIFEST))
    if edit:
        edit(files, manifest)
    if recompute:
        manifest["files"] = [{"path": p, "size": len(d), "sha256": sha(d)}
                             for p, d in files.items()]
    with zipfile.ZipFile(dest, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
        archive.writestr(backup.MANIFEST, json.dumps(manifest))
    return dest


class Restore:
    """Run RESTORE and check that a refusal left nothing behind."""

    def __init__(self, app):
        self.app = app
        self.names = app.storage.list_names()

    def refused(self, archive, line=None):
        before = archive.read_bytes() if archive.is_file() else None
        reply = run(self.app, line or f'RESTORE "{archive}" AS Copy')
        assert reply.startswith("! "), reply
        assert "nothing was restored" in reply.lower(), reply
        assert self.app.storage.list_names() == self.names
        assert self.app.storage.unsupported_module_dirs() == []
        assert staging(self.app) == []
        if before is not None:
            assert archive.read_bytes() == before
        return reply


def staging(app):
    """Staging folders left in modules/: a restore's short ones, and IMPORT's."""
    return [p.name for p in app.paths.modules_dir.iterdir()
            if p.name.startswith((".vs~", ".vocab-staging-"))]


def test_round_trip_restores_every_kind_of_study_data(app, tmp_path, documents_folder):
    folder = make_module(app, tmp_path)
    manifest_path = folder / "module.json"
    original = json.loads(manifest_path.read_text(encoding="utf-8"))
    original["extra"] = {"kept": True}  # a key this version does not know
    original["metadata"]["legacy_migration"] = {"source": "lists/Gatsby"}
    manifest_path.write_text(json.dumps(original), encoding="utf-8")
    run(app, "BACKUP Gatsby")
    source_tree = tree(app.paths.root)
    reply = run(app, f'RESTORE "Gatsby {DATE}.zip" as Copy')
    archive = documents_folder / f"Gatsby {DATE}.zip"
    assert reply == (f"Restored module 'Copy' from {archive} (2 words, 3 notes, 2 documents, "
                     "2 captures). USE it to start.")
    copy = app.paths.module_dir("Copy")
    files, folders = tree(folder)
    restored, restored_folders = tree(copy)
    assert set(restored) == set(files) and restored_folders == folders
    for path, data in files.items():
        if path != "module.json":
            assert restored[path] == data, path
    manifest = json.loads(restored["module.json"])
    assert manifest["name"] == "Copy" and manifest["created"] == original["created"]
    assert manifest["extra"] == {"kept": True}
    assert manifest["metadata"]["id"] != original["metadata"]["id"]
    assert "legacy_migration" not in manifest["metadata"]
    storage = app.storage
    assert storage.load_words("Copy").to_dict()["words"] == \
        storage.load_words("Gatsby").to_dict()["words"]
    assert storage.load_stats("Copy") == storage.load_stats("Gatsby")
    assert LearningStore(storage).load("Copy") == LearningStore(storage).load("Gatsby")
    for document in ("book.epub", "paper.pdf"):
        assert storage.load_reading_pos("Copy", document) == \
            storage.load_reading_pos("Gatsby", document)
    assert storage.note_names("Copy") == storage.note_names("Gatsby")
    assert storage.document_names("Copy") == storage.document_names("Gatsby")
    after = tree(app.paths.root)
    assert {k: v for k, v in after[0].items() if not k.startswith(os.path.join("modules", "Copy"))} \
        == source_tree[0]  # the original and the archive are unchanged


def test_restore_never_replaces_or_merges(app, tmp_path, documents_folder):
    archive = made_backup(app, tmp_path, documents_folder)
    app.cmd_create("Other")
    original = tree(app.paths.modules_dir)
    restore = Restore(app)
    reply = restore.refused(archive, f'RESTORE "{archive}"')
    assert reply == (f"! Module 'Gatsby' already exists; nothing was restored. Restore the "
                     f'backup under another name:  RESTORE "{archive}" AS <new name>')
    assert "Module 'Other' already exists" in restore.refused(archive, f'RESTORE "{archive}" as Other')
    probe = app.paths.modules_dir / "CaseProbe"
    probe.mkdir()
    insensitive = (app.paths.modules_dir / "caseprobe").exists()
    probe.rmdir()
    if insensitive:
        assert "already exists" in restore.refused(archive, f'RESTORE "{archive}" AS gatsby')
    (app.paths.modules_dir / "X").mkdir()
    (app.paths.modules_dir / "X" / "mine.txt").write_text("not a module", encoding="utf-8")
    restore.names = app.storage.list_names()
    reply = restore.refused(archive, f'RESTORE "{archive}" AS X')
    assert f"A folder named 'X' already exists in {app.paths.modules_dir} but is not a module" in reply
    assert (app.paths.modules_dir / "X" / "mine.txt").read_text(encoding="utf-8") == "not a module"
    for line, reason in ((f'RESTORE "{archive}" AS studyall', "reserved"),
                         (f'RESTORE "{archive}" AS a/b', "may only contain"),
                         (f'RESTORE "{archive}" AS .vocab-staging-{"0" * 32}',
                          "temporary folders")):
        assert reason in restore.refused(archive, line)
    current = tree(app.paths.modules_dir)
    assert {k: v for k, v in current[0].items() if not k.startswith("X")} == original[0]


def test_restored_module_is_a_new_module(app, tmp_path, documents_folder):
    archive = made_backup(app, tmp_path, documents_folder)
    run(app, f'RESTORE "{archive}" AS One')
    run(app, f'RESTORE "{archive}" AS Two')
    identities = {app.storage.module_identity(n) for n in ("Gatsby", "One", "Two")}
    assert len(identities) == 3
    assert len({created for created, _ in identities}) == 1  # 'created' is kept


def _flip_document_byte(src, dest):
    data = bytearray(src.read_bytes())
    with zipfile.ZipFile(src) as archive:
        info = archive.getinfo("module/documents/paper.pdf")
    start = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    data[start + info.file_size // 2] ^= 0xFF
    dest.write_bytes(bytes(data))


CORRUPT = {
    "random bytes": (lambda src, dest: dest.write_bytes(os.urandom(4000)),
                     "it is not a ZIP file, or it is damaged"),
    "empty": (lambda src, dest: dest.write_bytes(b""), "it is not a ZIP file, or it is damaged"),
    # zipfile may then find the end record of the EPUB stored inside instead.
    "truncated": (lambda src, dest: dest.write_bytes(src.read_bytes()[:len(src.read_bytes()) // 2]),
                  ("it is not a ZIP file, or it is damaged", "no vocab-study-backup.json")),
    "an export": (lambda src, dest: dest.write_text('{"words": {"words": {}}}', encoding="utf-8"),
                  "restored with IMPORT"),
    "an epub": (lambda src, dest: write_epub(dest), "no vocab-study-backup.json). A vocabulary "
                                                    "file from EXPORT is restored with IMPORT"),
    "manifest not json": (lambda src, dest: rewrite(src, dest, recompute=False) and _replace_member(
        dest, backup.MANIFEST, b"{not json"), "vocab-study-backup.json is not valid JSON"),
    "wrong format": (lambda src, dest: rewrite(src, dest, lambda f, m: m.update(format="other")),
                     "does not describe a Vocab Study module backup"),
    "version 2": (lambda src, dest: rewrite(src, dest, lambda f, m: m.update(version=2)),
                  "made by a newer version of Vocab Study (backup format 2; this version reads "
                  "format 1). Update Vocab Study, then restore it"),
    "version 0": (lambda src, dest: rewrite(src, dest, lambda f, m: m.update(version=0)),
                  "backup format version 0 is not one Vocab Study knows"),
    "version x": (lambda src, dest: rewrite(src, dest, lambda f, m: m.update(version="x")),
                  "backup format version 'x' is not one"),
    "flipped byte": (_flip_document_byte, "'module/documents/paper.pdf' is damaged (its "
                                          "contents do not match the checksum"),
    "listed file missing": (lambda src, dest: rewrite(src, dest, lambda f, m: f.pop(
        "module/documents/paper.pdf"), recompute=False), "'module/documents/paper.pdf' is "
                                                         "missing from it"),
    "size mismatch": (lambda src, dest: rewrite(src, dest, lambda f, m: [
        e.update(size=e["size"] + 1) for e in m["files"] if e["path"].endswith("words.json")],
        recompute=False), "'module/vocab/words.json' is damaged"),
    "deep nesting": (lambda src, dest: rewrite(src, dest, lambda f, m: f.update(
        {"module/learning.json": b"[" * 100_000})),
        "learning.json in the backup cannot be used: it is nested too deeply to read"),
}


def _replace_member(path, member, data):
    with zipfile.ZipFile(path) as archive:
        files = {i.filename: archive.read(i) for i in archive.infolist()}
    files[member] = data
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


@pytest.mark.parametrize("case", sorted(CORRUPT))
def test_corrupt_archives_are_refused_and_create_nothing(app, tmp_path, documents_folder, case):
    archive = made_backup(app, tmp_path, documents_folder)
    damage, reason = CORRUPT[case]
    bad = tmp_path / "bad.zip"
    damage(archive, bad)
    reply = Restore(app).refused(bad)
    assert reply.startswith(f"! Cannot restore {bad}: "), reply
    assert any(r in reply for r in ((reason,) if isinstance(reason, str) else reason)), reply


def test_an_unreadable_backup_is_not_called_damaged(app, tmp_path, documents_folder,
                                                    monkeypatch):
    archive = made_backup(app, tmp_path, documents_folder)
    restore = Restore(app)
    expected = f"! Could not restore {archive}: Permission denied. Nothing was restored."
    with monkeypatch.context() as patch:
        deny(patch, archive)  # no permission, or locked by another program
        assert restore.refused(archive) == expected
        with pytest.raises(ValueError) as refused:  # the desktop checks with backup_info
            app.backup_info(str(archive))
        assert f"! {refused.value}" == expected
    # A lock on part of the file shows once zipfile reads past the start.
    real_zipfile = zipfile.ZipFile

    def locked(file, *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and os.fspath(file) == os.fspath(archive):
            raise PermissionError(errno.EACCES, "Permission denied", os.fspath(archive))
        return real_zipfile(file, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(zipfile, "ZipFile", locked)
        assert restore.refused(archive) == expected
    assert run(app, f'RESTORE "{archive}" AS Copy').startswith("Restored module 'Copy'")


def test_missing_files_and_folders_are_named(app, tmp_path, documents_folder):
    restore = Restore(app)
    assert restore.refused(documents_folder / "gone.zip") == \
        f"! Backup file not found: {documents_folder / 'gone.zip'}. Nothing was restored."
    assert restore.refused(tmp_path, f'RESTORE "{tmp_path}" AS Copy') == \
        f"! Cannot restore {tmp_path}: it is a folder, not a backup file. Nothing was restored."


def _set_central_flag(path, member, bit):
    data = bytearray(path.read_bytes())
    offset = 0
    while True:
        offset = data.find(b"PK\x01\x02", offset)
        assert offset != -1, member
        length = int.from_bytes(data[offset + 28:offset + 30], "little")
        if data[offset + 46:offset + 46 + length].decode() == member:
            data[offset + 8] |= bit
            break
        offset += 4
    path.write_bytes(bytes(data))


def test_tampered_archives_are_refused(app, tmp_path, documents_folder):
    archive = made_backup(app, tmp_path, documents_folder)
    restore = Restore(app)
    bad = tmp_path / "bad.zip"

    def check(reason):
        reply = restore.refused(bad)
        assert reply.startswith(f"! Cannot restore {bad}: "), reply
        assert reason in reply, reply
    rewrite(archive, bad, lambda f, m: f.update({"module/notes/Ch1.md": b"changed"}),
            recompute=False)
    check("'module/notes/Ch1.md' is damaged")
    rewrite(archive, bad, lambda f, m: f.update({"module/notes/extra.md": b"x"}), recompute=False)
    check("'module/notes/extra.md', which its vocab-study-backup.json does not list")
    for member, content, reason in (
            ("module/learning.json", b'{"schema": 1, "items": {"a": {"id": "a"}}}',
             "learning.json in the backup cannot be used: capture 'a': 'kind' must be concept"),
            ("module/vocab/stats.json", b'{"ephemeral": {"due": "tomorrow"}}',
             "vocab/stats.json in the backup cannot be used: progress for 'ephemeral': 'due'"),
            ("module/vocab/words.json",
             b'{"schema": 1, "words": {"x": {"senses": [{"definition": ""}]}}}',
             "vocab/words.json in the backup cannot be used: word 'x': a definition is empty"),
            ("module/module.json", b"[]",
             "module.json in the backup cannot be used: expected a JSON object")):
        rewrite(archive, bad, lambda f, m: f.update({member: content}))
        check(reason)
    import warnings
    rewrite(archive, bad)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(bad, "a") as zipped:
            zipped.writestr("module/notes/Ch1.md", b"a second copy")
    check("'module/notes/Ch1.md' twice")
    rewrite(archive, bad)
    _set_central_flag(bad, "module/notes/Ch1.md", 0x1)
    check("it is encrypted")

    def add_link(files, manifest):
        files["module/documents/link.pdf"] = b"/etc/passwd"
    rewrite(archive, bad, add_link)
    with zipfile.ZipFile(bad) as zipped:
        files = {i.filename: zipped.read(i) for i in zipped.infolist()}
    with zipfile.ZipFile(bad, "w") as zipped:
        for name, data in files.items():
            info = zipfile.ZipInfo(name)
            if name.endswith("link.pdf"):
                info.external_attr = (0o120777) << 16  # a symbolic link
            zipped.writestr(info, data)
    check("'module/documents/link.pdf' is a link")


TRAVERSAL = [
    "../evil.txt",
    "module/documents/../../evil.txt",
    "/tmp/vocab-evil-absolute.txt",
    "module/documents/sub/evil.pdf",
    "module/notes/x.txt",
    f"module/documents/.vocab-staging-{'a' * 32}",
    "module/documents/nul\x00evil.pdf",
    "module/documents/ctl\x01evil.pdf",
    "module/extra.json",
    pytest.param("module/documents/CON.pdf", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="a Windows device name")),
    pytest.param("module/documents/a:b.pdf", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="a Windows-only invalid name")),
    pytest.param("module/documents/a\\b.pdf", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="a Windows separator")),
    pytest.param("module/documents/trailing.", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="a Windows-only invalid name")),
]


@pytest.mark.parametrize("member", TRAVERSAL)
def test_path_traversal_and_unsafe_names_are_refused(app, tmp_path, documents_folder, member):
    archive = made_backup(app, tmp_path, documents_folder)
    bad = tmp_path / "bad.zip"
    rewrite(archive, bad, lambda f, m: f.update({member: b"evil"}))
    before = tree(tmp_path)
    reply = Restore(app).refused(bad)
    assert reply.startswith(f"! Cannot restore {bad}: it holds "), reply
    assert tree(tmp_path) == before  # nothing written anywhere
    assert not os.path.exists("/tmp/vocab-evil-absolute.txt")


def test_archive_limits_are_checked_before_writing(app, tmp_path, documents_folder,
                                                   monkeypatch):
    archive = made_backup(app, tmp_path, documents_folder)
    restore = Restore(app)
    with monkeypatch.context() as patch:
        patch.setattr(backup, "MAX_ENTRIES", 5)
        assert "it holds 11 files; a backup holds at most 5" in restore.refused(archive)
    with monkeypatch.context() as patch:
        patch.setattr(backup, "MAX_MANIFEST_BYTES", 100)
        assert "vocab-study-backup.json is larger than 100 bytes" in restore.refused(archive)
    with monkeypatch.context() as patch:
        patch.setattr(backup, "MAX_JSON_BYTES", 150)
        assert "is larger than 150 bytes, the most a backup holds" in restore.refused(archive)
    with monkeypatch.context() as patch:
        import shutil
        usage = shutil.disk_usage(app.paths.modules_dir)
        patch.setattr(backup.shutil, "disk_usage",
                      lambda path: usage._replace(free=backup.SPACE_MARGIN + 1000))
        reply = restore.refused(archive)
        total = sum(f["size"] for f in backup.read_backup(archive).files.values())
        assert (f"it needs about {backup.size_text(total)} free in {app.paths.modules_dir}, and "
                f"{backup.size_text(backup.SPACE_MARGIN + 1000)} is free") in reply

    class Inflated(io.BytesIO):
        consumed = 0

        def read(self, size=-1):
            data = super().read(size)
            self.consumed += len(data)
            return data
    inflated = Inflated(b"\0" * 1_000_000)
    real_open = zipfile.ZipFile.open

    def lying_open(self, name, mode="r", *args, **kwargs):
        if getattr(name, "filename", name) == "module/documents/paper.pdf":
            return inflated
        return real_open(self, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", lying_open)
    assert "'module/documents/paper.pdf' is damaged" in restore.refused(archive)
    assert inflated.consumed == (256 * 64) + 1  # stopped at the first byte too many


def test_failed_restore_leaves_nothing_and_a_retry_succeeds(app, tmp_path, documents_folder,
                                                            monkeypatch):
    archive = made_backup(app, tmp_path, documents_folder)
    restore = Restore(app)
    monkeypatch.setattr(backup, "CHUNK", 1024)
    real_write = backup._write_chunk
    documents = []

    def full_disk(stream, data):
        name = getattr(stream, "name", "")
        if "documents" in str(name) and name not in documents:
            documents.append(name)
        if len(documents) == 2:
            raise OSError(errno.ENOSPC, "No space left on device")
        real_write(stream, data)
    monkeypatch.setattr(backup, "_write_chunk", full_disk)
    assert restore.refused(archive) == \
        f"! Could not restore {archive}: No space left on device. Nothing was restored."
    monkeypatch.setattr(backup, "_write_chunk", real_write)

    def no_rename(staging, final):
        raise PermissionError(errno.EACCES, "Access is denied", str(staging))
    monkeypatch.setattr(backup, "_rename_into_place", no_rename)
    assert "Could not restore" in restore.refused(archive)
    monkeypatch.undo()

    other = App(app.paths.root)  # another window or process

    def created_meanwhile(done, total, label):
        if done and not other.storage.exists("Copy"):
            other.cmd_create("Copy")
    reply = run_restore(app, archive, "Copy", created_meanwhile)
    assert reply.startswith("! Module 'Copy' was created while the backup was being restored; "
                            "nothing was restored."), reply
    assert app.storage.load_words("Copy").words == {}  # the other window's module is intact
    assert staging(app) == []
    assert run(app, f'RESTORE "{archive}" AS Copy2').startswith("Restored module 'Copy2'")


def limit_path_length(monkeypatch, limit):
    """Make creating a file or folder whose path is longer than ``limit``
    fail as Windows does without long paths (ENOENT)."""
    real_open, real_mkdir = open, os.mkdir

    def check(path):
        if len(os.fspath(path)) > limit:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", os.fspath(path))

    def limited_open(file, mode="r", *args, **kwargs):
        if isinstance(file, (str, os.PathLike)) and set(mode) & set("wxa+"):
            check(file)
        return real_open(file, mode, *args, **kwargs)

    def limited_mkdir(path, *args, **kwargs):
        check(path)
        return real_mkdir(path, *args, **kwargs)
    monkeypatch.setattr("builtins.open", limited_open)
    monkeypatch.setattr(os, "mkdir", limited_mkdir)


def test_staged_paths_are_no_longer_than_the_final_ones(app, tmp_path, documents_folder,
                                                        monkeypatch):
    app.cmd_create("Gatsby")
    long_name = "A" * 60 + ".pdf"
    (tmp_path / long_name).write_bytes(b"%PDF-1.4 long")
    app.storage.add_document("Gatsby", tmp_path / long_name)
    archive = documents_folder / "g.zip"
    assert run(app, f'BACKUP Gatsby "{archive}"').startswith("Backed up")
    modules = app.paths.modules_dir
    # A module name of 12 characters or more: the restore needs no path
    # longer than the module's own.
    name = "Gatsby copy2"
    longest = modules / name / "documents" / long_name
    with monkeypatch.context() as patch:
        limit_path_length(patch, len(str(longest)))
        assert run(app, f'RESTORE "{archive}" AS {name}').startswith(f"Restored module '{name}'")
    assert longest.read_bytes() == b"%PDF-1.4 long"
    assert staging(app) == []


def test_a_write_error_while_restoring_is_not_a_missing_backup(app, tmp_path, documents_folder,
                                                               monkeypatch):
    app.cmd_create("Gatsby")
    long_name = "A" * 60 + ".pdf"
    (tmp_path / long_name).write_bytes(b"%PDF-1.4 long")
    app.storage.add_document("Gatsby", tmp_path / long_name)
    archive = documents_folder / "g.zip"
    assert run(app, f'BACKUP Gatsby "{archive}"').startswith("Backed up")
    modules = app.paths.modules_dir
    restore = Restore(app)
    # A shorter name: the staged document path is a few characters longer
    # than the final one, and over the limit.
    limit = len(str(modules / "C" / "documents" / long_name)) + 5
    staged = re.escape(str(modules)) + re.escape(os.sep) + r"\.vs~[0-9a-f]{8}" + re.escape(
        os.sep + "documents" + os.sep + long_name)
    with monkeypatch.context() as patch:
        limit_path_length(patch, limit)
        patch.setattr(backup, "_MAX_PATH", None, raising=False)  # not Windows
        reply = restore.refused(archive, f'RESTORE "{archive}" AS C')
        assert re.fullmatch(re.escape(f"! Could not restore {archive}: No such file or directory (")
                            + staged + re.escape("). Nothing was restored."), reply), reply
        patch.setattr(backup, "_MAX_PATH", limit + 1, raising=False)  # Windows, short paths
        reply = restore.refused(archive, f'RESTORE "{archive}" AS C')
        length = limit + 6
        assert re.fullmatch(re.escape(f"! Could not restore {archive}: No such file or directory (")
                            + staged + re.escape(f": {length} characters, longer than Windows "
                                                 "allows unless long paths are turned on). "
                                                 "Nothing was restored."), reply), reply
    # The modules folder vanishing meanwhile is not a missing backup either.
    def vanished(path):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path))
    with monkeypatch.context() as patch:
        patch.setattr(backup, "_create", vanished)
        reply = restore.refused(archive)
        assert reply.startswith(f"! Could not restore {archive}: No such file or directory ("), reply
    assert restore.refused(documents_folder / "gone.zip") == \
        f"! Backup file not found: {documents_folder / 'gone.zip'}. Nothing was restored."


def test_a_restores_staging_folder_is_hidden_and_purged_once_abandoned(app, tmp_path):
    app.cmd_create("Book")
    modules = app.paths.modules_dir
    abandoned, fresh = modules / ".vs~0123abcd", modules / ".vs~89abcdef"
    for folder in (abandoned, fresh):
        (folder / "vocab").mkdir(parents=True)
        (folder / "module.json").write_text("{}", encoding="utf-8")
    old = abandoned.stat().st_mtime - 2 * 24 * 3600
    os.utime(abandoned, (old, old))
    assert app.storage.list_names() == ["Book"]
    assert app.storage.unsupported_module_dirs() == []
    App(app.paths.root)  # a start purges leftovers
    assert not abandoned.exists() and fresh.exists()
    # Only that exact form, and only in the modules folder: a document may
    # have any name.
    (tmp_path / ".vs~0123abcd").write_bytes(b"a document")
    app.storage.add_document("Book", tmp_path / ".vs~0123abcd")
    document = app.paths.documents_dir("Book") / ".vs~0123abcd"
    os.utime(document, (old, old))
    App(app.paths.root)
    assert app.storage.document_names("Book") == [".vs~0123abcd"]


def run_restore(app, archive, name, progress):
    try:
        return app.cmd_restore(str(archive), name, progress=progress)
    except ValueError as error:
        return f"! {error}"


@pytest.mark.parametrize("stop", [backup.Cancelled, KeyboardInterrupt])
def test_cancelled_restore_leaves_nothing(app, tmp_path, documents_folder, stop):
    archive = made_backup(app, tmp_path, documents_folder)
    names = app.storage.list_names()

    def cancel(done, total, label):
        if done:
            raise stop()
    assert run_restore(app, archive, "Copy", cancel) == "Restore cancelled; nothing was restored."
    assert app.storage.list_names() == names and staging(app) == []
    assert run_restore(app, archive, "Copy", None).startswith("Restored module 'Copy'")


def test_an_interrupt_after_the_module_is_in_place_reports_it(app, tmp_path, documents_folder,
                                                               monkeypatch):
    archive = made_backup(app, tmp_path, documents_folder)
    real = backup._rename_into_place

    def renamed_then_interrupted(staging_folder, final):
        real(staging_folder, final)
        raise KeyboardInterrupt
    monkeypatch.setattr(backup, "_rename_into_place", renamed_then_interrupted)
    assert run_restore(app, archive, "Copy", None).startswith("Restored module 'Copy' from ")
    assert app.storage.module_identity("Copy") != app.storage.module_identity("Gatsby")
    assert staging(app) == []


def test_restore_dispatch_parsing(app, tmp_path, documents_folder, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "cmd_restore", lambda src, name=None, **kw: calls.append((src, name))
                        or "ran")
    usage = "Usage: RESTORE <file.zip> [as <name>]   (quote a path that contains spaces)"
    for line in ("RESTORE", "RESTORE a.zip as", "RESTORE a.zip to Copy", "RESTORE a b"):
        assert run(app, line) == usage, line
    assert "cannot start or end with a quote" in run(app, "RESTORE 'a.zip'")
    run(app, 'RESTORE "My Backups/Gatsby 2026.zip" As The Great Gatsby')
    run(app, r"RESTORE C:\Backups\x.zip")
    assert calls == [("My Backups/Gatsby 2026.zip", "The Great Gatsby"),
                     (r"C:\Backups\x.zip", None)]


def test_large_restores_announce_themselves_in_the_terminal(app, tmp_path, documents_folder,
                                                            monkeypatch):
    archive = made_backup(app, tmp_path, documents_folder)
    import vocab.cli.repl as repl_module
    monkeypatch.setattr(repl_module, "PROGRESS_THRESHOLD", 1000)
    out = io.StringIO()
    reply = Repl(app, in_stream=io.StringIO(), out_stream=out).dispatch(
        parse(f'RESTORE "{archive}" AS Copy'))
    assert reply.startswith("Restored module 'Copy'")
    assert out.getvalue().startswith("Restoring 'Copy' (")
    assert out.getvalue().rstrip().endswith("... Ctrl-C cancels.")


def test_restored_epub_capture_still_highlights_its_passage(tmp_path):
    # Real data end to end: a capture made from a selection in a real reader,
    # backed up, restored under another name, and opened from the copy.
    pytest.importorskip("PySide6")
    from .test_epub_highlights import run_browser
    run_browser(tmp_path, r'''
from pathlib import Path
viewer = open_viewer()
source = capture_source(viewer)
item = save_capture("Book", quote, source)
expect_marks(viewer, [("second", quote)])
archive = Path(sys.argv[1]) / "Book backup.zip"
reply = data.cmd_backup("Book", str(archive))
assert reply.startswith("Backed up 'Book'"), reply
reply = data.cmd_restore(str(archive), "Copy")
assert reply.startswith("Restored module 'Copy'"), reply
restored = store.load("Copy")[item["id"]]
assert restored["source"] == source and restored["source"]["anchor"] == source["anchor"]
copy = open_viewer("Copy", "book.epub", source=dict(restored["source"], capture_id=item["id"]))
expect_marks(copy, [("second", quote)])
wait_for(lambda: js(copy, """(() => {
    const ranges = Array.from(CSS.highlights.get('vocab-study-active') || []);
    return ranges.length === 1 &&
        ranges[0].startContainer.parentElement.closest('p').id === 'second';
})()"""))
assert copy._pending_highlight is None
''')


# ---- BACKUPALL --------------------------------------------------------------------
FOLDER = f"Vocab Study backup {DATE}"


def test_backupall_writes_one_restorable_archive_per_module(app, tmp_path, documents_folder):
    for sources in ("a", "b", "c"):  # each module's source files
        (tmp_path / sources).mkdir()
    make_module(app, tmp_path / "a", "Alpha")
    make_module(app, tmp_path / "b", "Beta")
    folder_c = make_module(app, tmp_path / "c", "Gamma")
    (folder_c / "learning.json").write_text('{"schema": 1, "items": {"x": 1}}', encoding="utf-8")
    unsupported = os.name != "nt"
    if unsupported:
        bad = app.paths.modules_dir / "bad name?"
        (bad / "vocab").mkdir(parents=True)
        (bad / "vocab" / "words.json").write_text('{"words": {}}', encoding="utf-8")
    reply = run(app, "BACKUPALL")
    folder = documents_folder / FOLDER
    lines = reply.splitlines()
    assert lines[0] == f"Backed up 2 of {4 if unsupported else 3} modules -> {folder}"
    assert lines[1].startswith("  Alpha.zip (2 words, 3 notes, 2 documents, 2 captures; ")
    assert lines[2].startswith("  Beta.zip (2 words, 3 notes, 2 documents, 2 captures; ")
    assert (f"! Not backed up: Gamma: Could not back up 'Gamma': Could not read "
            f"{folder_c / 'learning.json'}: capture 'x': it is not an object.") in reply
    if unsupported:
        assert ("! Not backed up: bad name? (folder name not supported; rename it in the "
                "modules folder)") in reply
    assert sorted(p.name for p in folder.iterdir()) == ["Alpha.zip", "Beta.zip"]
    for name in ("Alpha", "Beta"):
        assert run(app, f'RESTORE "{folder / (name + ".zip")}" AS {name} again').startswith(
            f"Restored module '{name} again'")
    assert f"-> {documents_folder / (FOLDER + ' (2)')}" in run(app, "BACKUPALL")
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    assert f"-> {shelf / FOLDER}" in run(app, f"BACKUPALL {shelf}")


def test_backupall_keeps_going_when_a_module_disappears(app, tmp_path, documents_folder):
    for name in ("A", "B", "C"):
        app.cmd_create(name)
        app.cmd_use(name)
        app.cmd_add("word", manual_def="a definition")
    other = App(app.paths.root)  # another window

    def delete_b(done, total, label):
        if "'A'" in label and other.storage.exists("B"):
            other.cmd_delete("B")
    reply = app.cmd_backup_all(progress=delete_b)
    assert reply.startswith(f"Backed up 2 of 3 modules -> {documents_folder / FOLDER}")
    assert "! Not backed up: B: Module 'B' does not exist." in reply
    assert sorted(p.name for p in (documents_folder / FOLDER).iterdir()) == ["A.zip", "C.zip"]


@pytest.mark.parametrize("stop", [backup.Cancelled, KeyboardInterrupt])
def test_backupall_cancel_keeps_complete_archives_only(app, tmp_path, documents_folder, stop):
    for name in ("A", "B", "C"):
        (tmp_path / name).mkdir()
        make_module(app, tmp_path / name, name)

    def cancel(done, total, label):
        if "'B'" in label and done:
            raise stop()
    reply = app.cmd_backup_all(progress=cancel)
    folder = documents_folder / FOLDER
    assert reply.splitlines()[0] == (f"Cancelled after backing up 1 of 3 modules to {folder}; "
                                     "the archives there are complete.")
    assert [p.name for p in folder.iterdir()] == ["A.zip"]
    assert backup.read_backup(folder / "A.zip").module == "A"

    def cancel_first(done, total, label):
        raise stop()
    assert app.cmd_backup_all(progress=cancel_first) == "Backup cancelled; nothing was written."
    assert sorted(p.name for p in documents_folder.iterdir()) == [FOLDER]


def test_backupall_with_no_modules_or_all_failing_leaves_no_folder(app, tmp_path,
                                                                   documents_folder):
    assert run(app, "BACKUPALL") == "No modules to back up."
    assert list(documents_folder.iterdir()) == []
    app.cmd_create("Broken")
    (app.paths.module_dir("Broken") / "reading.json").write_text("{bad", encoding="utf-8")
    reply = run(app, "BACKUPALL")
    assert reply.startswith("No module was backed up; nothing was written.\n! Not backed up: "
                            "Broken: Could not back up 'Broken': Could not read ")
    assert list(documents_folder.iterdir()) == []
    assert run(app, f'BACKUPALL "{tmp_path / "missing"}"').startswith(
        f"! Could not write to {tmp_path / 'missing'}: that folder does not exist.")


def test_backupall_usage(app, tmp_path, documents_folder):
    assert run(app, "BACKUPALL a b") == ("Usage: BACKUPALL [<folder>]   (quote a path that "
                                         "contains spaces)")
    assert "cannot start or end with a quote" in run(app, "BACKUPALL 'shelf'")
    assert list(documents_folder.iterdir()) == []
