"""Deleting a module either fully succeeds or leaves the module intact."""
import os
from pathlib import Path

import pytest

from vocab.cli.app import App
from vocab.core import storage as storage_module


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_add("word", target="Book", manual_def="meaning")
    app.storage.save_note("Book", "One", "a note")
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.4 content")
    app.storage.add_document("Book", source)
    app.cmd_use("Book")
    return app


def files(app):
    folder = app.paths.module_dir("Book")
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


def hidden(app):
    return sorted(p.name for p in app.paths.modules_dir.iterdir() if p.name.startswith("."))


def test_a_failed_move_deletes_nothing(app, monkeypatch):
    before = files(app)
    def locked(self, target):
        raise PermissionError(32, "The process cannot access the file")
    monkeypatch.setattr(Path, "rename", locked)
    with pytest.raises(OSError, match="nothing was deleted"):
        app.cmd_delete("Book")
    monkeypatch.undo()
    assert files(app) == before
    assert app.storage.list_names() == ["Book"] and app.current_module == "Book"


def test_a_failed_cleanup_leaves_no_half_module_and_is_purged_later(app, monkeypatch):
    def remove_one_file_then_fail(folder, **kwargs):
        next(p for p in Path(folder).rglob("*.md")).unlink()
        raise OSError("file in use")
    monkeypatch.setattr(storage_module.shutil, "rmtree", remove_one_file_then_fail)
    app.cmd_delete("Book")
    monkeypatch.undo()
    assert app.storage.list_names() == [] and not app.storage.exists("Book")
    assert app.current_module is None
    assert len(hidden(app)) == 1 and hidden(app)[0].startswith(".vocab-trash-")
    app.cmd_create("Book")  # the name is free again
    assert app.storage.load_words("Book").words == {}
    App(app.paths.root)  # a later start purges the leftover
    assert hidden(app) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only files")
def test_read_only_files_do_not_stop_deletion(app):
    document = app.paths.documents_dir("Book") / "doc.pdf"
    document.chmod(0o444)
    app.cmd_delete("Book")
    assert not app.storage.exists("Book") and hidden(app) == []


TRASH = ".vocab-trash-" + "a" * 32
STAGING = ".vocab-staging-" + "b" * 32


def test_leftover_names_are_never_listed(app):
    (app.paths.modules_dir / TRASH / "vocab").mkdir(parents=True)
    (app.paths.modules_dir / TRASH / "module.json").write_text("{}")
    (app.paths.modules_dir / STAGING).mkdir()
    assert app.storage.list_names() == ["Book"]
    assert app.storage.unsupported_module_dirs() == []


def test_user_modules_that_only_look_like_leftovers_are_kept(app):
    app.cmd_create(".vocab-trash-notes")
    App(app.paths.root)  # a start purges leftovers
    assert app.storage.list_names() == [".vocab-trash-notes", "Book"]
    with pytest.raises(ValueError, match="reserved"):
        app.cmd_create(TRASH)


def age(path, seconds=2 * 24 * 3600):
    old = path.stat().st_mtime - seconds
    os.utime(path, (old, old))


def test_only_abandoned_staging_is_purged(app):
    modules = app.paths.modules_dir
    (modules / STAGING).mkdir()
    fresh = modules / (".vocab-staging-" + "c" * 32)
    fresh.mkdir()  # possibly another process still building it
    age(modules / STAGING)
    App(app.paths.root)
    assert hidden(app) == [fresh.name]


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions enforced for this user")
def test_one_unreadable_module_does_not_stop_the_purge(app):
    app.cmd_create("Locked")
    (app.paths.modules_dir / TRASH).mkdir()
    locked = app.paths.module_dir("Locked")
    locked.chmod(0)
    try:
        App(app.paths.root)
        assert TRASH not in hidden(app)
    finally:
        locked.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")
def test_a_linked_module_is_not_deleted_through_its_link(app, tmp_path):
    outside = tmp_path / "elsewhere"
    app.paths.module_dir("Book").rename(outside)
    app.paths.module_dir("Book").symlink_to(outside, target_is_directory=True)
    mode = outside.stat().st_mode
    with pytest.raises(OSError, match="is a link.*nothing was deleted"):
        app.cmd_delete("Book")
    assert app.storage.list_names() == ["Book"] and hidden(app) == []
    App(app.paths.root)
    assert outside.stat().st_mode == mode
    assert (outside / "documents" / "doc.pdf").read_bytes() == b"%PDF-1.4 content"


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions enforced for this user")
def test_read_only_folders_do_not_stop_deletion(app, tmp_path):
    documents = app.paths.documents_dir("Book")
    (documents / "doc.pdf").chmod(0o444)
    documents.chmod(0o555)
    app.cmd_delete("Book")
    assert not app.storage.exists("Book") and hidden(app) == []


pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from .test_gui_reliability import qt, window  # noqa: E402,F401
from vocab.gui.viewers import PdfViewer  # noqa: E402


def open_pdf(window):
    from PySide6.QtGui import QPainter, QPdfWriter
    path = window.app.paths.documents_dir("Book") / "doc.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    painter = QPainter(writer)
    painter.drawText(100, 100, "page")
    painter.end()
    viewer = window._open_viewer(str(path))
    assert isinstance(viewer, PdfViewer)
    return viewer


def test_cancelling_a_console_delete_keeps_the_readers_open(window, monkeypatch):
    open_pdf(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.No)
    window.workspace.split_vertical()
    window.workspace.set_focused_component("console")
    console = window.workspace.focused.component
    console.cmd.setText("DELETE Book")
    console._run()
    assert "PdfViewer" in [type(pane.component).__name__ for pane in window.workspace._panes]
    assert window.app.storage.exists("Book")


@pytest.mark.parametrize("route", ["sidebar", "console"])
def test_readers_are_closed_before_the_module_moves(window, monkeypatch, route):
    open_pdf(window)
    seen = []
    delete = window.app.storage.delete
    def record_then_delete(name):
        seen.append([type(pane.component).__name__ for pane in window.workspace._panes])
        return delete(name)
    monkeypatch.setattr(window.app.storage, "delete", record_then_delete)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    if route == "sidebar":
        window._delete_module()
    else:
        window.workspace.split_vertical()  # the reader stays open in its own pane
        window.workspace.set_focused_component("console")
        console = window.workspace.focused.component
        console.cmd.setText("DELETE Book")
        console._run()
    assert seen and "PdfViewer" not in seen[0]
    if route == "console":
        assert "ConsoleComponent" in seen[0] and "DocumentsComponent" in seen[0]
    assert not window.app.storage.exists("Book")
