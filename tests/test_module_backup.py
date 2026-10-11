"""Module backups: BACKUP writes one archive holding a whole module.

Core and command tests without Qt. Archives go to the folder for files,
which the conftest points at a temporary Documents folder.
"""
import errno
import hashlib
import io
import json
import os
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
