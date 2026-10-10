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


def test_leftover_names_are_never_listed(app):
    (app.paths.modules_dir / ".vocab-trash-abc" / "vocab").mkdir(parents=True)
    (app.paths.modules_dir / ".vocab-trash-abc" / "module.json").write_text("{}")
    (app.paths.modules_dir / ".vocab-staging-def").mkdir()
    assert app.storage.list_names() == ["Book"]
    assert app.storage.unsupported_module_dirs() == []


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
