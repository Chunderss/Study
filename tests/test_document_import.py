"""Adding a document never leaves a partial copy, and never replaces a file."""
import os
import shutil
import threading

import pytest

from vocab.cli.app import App
from vocab.core.locking import write_lock
from vocab.core import storage as storage_module


@pytest.fixture
def setup(tmp_path):
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    source = tmp_path / "book.epub"
    source.write_bytes(b"EPUB" * 4096)
    return app, source


def documents(app):
    return sorted(p.name for p in app.paths.documents_dir("Book").iterdir())


def test_adding_publishes_one_complete_copy(setup):
    app, source = setup
    assert app.storage.add_document("Book", source) == "book.epub"
    assert documents(app) == ["book.epub"]  # no staging leftovers
    assert (app.paths.documents_dir("Book") / "book.epub").read_bytes() == source.read_bytes()


def fail_part_way(src, dst, *args, **kwargs):
    with open(dst, "wb") as out:
        out.write(b"EPUB" * 5)
    raise OSError("disk full during copy")


@pytest.mark.parametrize("stage", ["copy", "flush", "publish"])
def test_a_failure_leaves_nothing_visible_and_a_retry_works(setup, monkeypatch, stage):
    app, source = setup
    if stage == "copy":
        monkeypatch.setattr(storage_module.shutil, "copyfile", fail_part_way)
    elif stage == "flush":
        monkeypatch.setattr(storage_module.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("flush failed")))
    else:
        monkeypatch.setattr(storage_module.os, "link", lambda *a: (_ for _ in ()).throw(OSError("no links")))
        monkeypatch.setattr(storage_module.os, "rename", lambda *a: (_ for _ in ()).throw(OSError("rename failed")))
    with pytest.raises(OSError):
        app.storage.add_document("Book", source)
    assert documents(app) == [] and app.storage.document_names("Book") == []
    monkeypatch.undo()
    app.storage.add_document("Book", source)
    assert documents(app) == ["book.epub"]


def test_a_document_appearing_meanwhile_is_kept(setup, monkeypatch):
    app, source = setup
    copy = shutil.copyfile
    def someone_else_adds_it_first(src, dst, *args, **kwargs):
        (app.paths.documents_dir("Book") / "book.epub").write_bytes(b"theirs")
        return copy(src, dst)
    monkeypatch.setattr(storage_module.shutil, "copyfile", someone_else_adds_it_first)
    with pytest.raises(FileExistsError):
        app.storage.add_document("Book", source)
    assert documents(app) == ["book.epub"]
    assert (app.paths.documents_dir("Book") / "book.epub").read_bytes() == b"theirs"


def test_existing_documents_keep_their_bytes(setup, tmp_path):
    app, source = setup
    app.storage.add_document("Book", source)
    other = tmp_path / "other" / "book.epub"
    other.parent.mkdir()
    other.write_bytes(b"different")
    with pytest.raises(FileExistsError):
        app.storage.add_document("Book", other)
    assert (app.paths.documents_dir("Book") / "book.epub").read_bytes() == source.read_bytes()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions enforced for this user")
def test_a_read_only_source_gives_a_writable_copy(setup):
    app, source = setup
    source.chmod(0o444)
    app.storage.add_document("Book", source)
    assert os.access(app.paths.documents_dir("Book") / "book.epub", os.W_OK)


@pytest.mark.parametrize("name", [".notes.pdf", ".vocab-staging-notes.pdf"])
def test_dot_named_documents_are_still_listed_and_kept(setup, tmp_path, name):
    app, _ = setup
    hidden = tmp_path / name
    hidden.write_bytes(b"%PDF")
    app.storage.add_document("Book", hidden)
    added = app.paths.documents_dir("Book") / name
    old = added.stat().st_mtime - 2 * 24 * 3600
    os.utime(added, (old, old))
    App(app.paths.root)  # a start purges abandoned staging copies
    assert app.storage.document_names("Book") == [name]


def test_the_staging_name_itself_is_refused(setup, tmp_path):
    app, _ = setup
    reserved = tmp_path / (".vocab-staging-" + "0" * 32)
    reserved.write_bytes(b"%PDF")
    with pytest.raises(ValueError, match="reserves"):
        app.storage.add_document("Book", reserved)
    assert documents(app) == []


def test_a_module_deleted_meanwhile_is_not_recreated(setup, monkeypatch):
    app, source = setup
    app.storage.delete("Book")
    monkeypatch.setattr(app.storage, "exists", lambda name: True)  # checked just before the delete
    with pytest.raises(OSError):
        app.storage.add_document("Book", source)
    assert not app.paths.module_dir("Book").exists()


def test_the_write_lock_is_free_while_copying(setup, monkeypatch):
    app, source = setup
    copy = shutil.copyfile
    other_writer = []
    def copy_while_someone_saves(src, dst, *args, **kwargs):
        thread = threading.Thread(target=lambda: other_writer.append(_try_lock(app)))
        thread.start()
        thread.join(5)
        return copy(src, dst)
    monkeypatch.setattr(storage_module.shutil, "copyfile", copy_while_someone_saves)
    app.storage.add_document("Book", source)
    assert other_writer == [True]


def _try_lock(app):
    with write_lock(app.paths.root).hold(timeout=0.5):
        return True


def test_copy_rejects_a_replacement_module_at_the_same_path(setup, monkeypatch):
    app, source = setup
    original = app.storage.module_identity("Book")
    started, resume = threading.Event(), threading.Event()
    copy = shutil.copyfile
    outcome = []
    def paused_copy(src, dst, *args, **kwargs):
        started.set()  # before opening the staging file
        assert resume.wait(10), "test did not release the document copy"
        return copy(src, dst, *args, **kwargs)
    def add():
        try:
            outcome.append(app.storage.add_document("Book", source))
        except Exception as error:
            outcome.append(error)
    monkeypatch.setattr(storage_module.shutil, "copyfile", paused_copy)
    worker = threading.Thread(target=add)
    worker.start()
    try:
        assert started.wait(10)
        app.cmd_delete("Book")
        app.cmd_create("Book")
        assert app.storage.module_identity("Book") != original
        replacement = app.paths.module_dir("Book")
        before = {p.relative_to(replacement): p.read_bytes()
                  for p in replacement.rglob("*") if p.is_file()}
    finally:
        resume.set()
        worker.join(10)
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], ValueError)
    assert "was replaced" in str(outcome[0])
    assert documents(app) == []
    assert {p.relative_to(replacement): p.read_bytes()
            for p in replacement.rglob("*") if p.is_file()} == before
