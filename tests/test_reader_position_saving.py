"""A reader whose place cannot be saved says so, and can stay open to retry."""
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox, QWidget

from .test_gui_reliability import qt, window  # noqa: F401
from .test_learning_gui import make_pdf
from vocab.gui.viewers import PdfViewer
from vocab.gui.workspace import Workspace


@pytest.fixture
def broken_storage(window):
    """Saving a reading position fails until repair() is called."""
    storage = window.app.storage

    def fail(*args, **kwargs):
        raise OSError("disk full")
    storage.save_reading_pos = fail
    repair = lambda: storage.__dict__.pop("save_reading_pos", None)
    yield repair
    repair()  # before the window closes at teardown


def open_pdf(window):
    make_pdf(window)
    viewer = window._open_viewer(str(window.app.paths.documents_dir("Book") / "book.pdf"))
    assert isinstance(viewer, PdfViewer)
    return viewer


def readers(window):
    return [pane.component for pane in window.workspace._panes
            if isinstance(pane.component, PdfViewer)]


def leave(window, viewer, route):
    if route == "switch":
        window.workspace.set_focused_component("notes")
    elif route == "back":
        viewer.back_requested.emit()
    elif route == "close_pane":
        pane = window.workspace.focused
        window.workspace.split_vertical()
        window.workspace.focus_pane(pane)
        window.workspace.close_focused()
    else:
        window.close()


ROUTES = ["switch", "back", "close_pane", "quit"]


@pytest.mark.parametrize("route", ROUTES)
def test_a_failed_save_is_shown_and_staying_keeps_the_reader(window, broken_storage, monkeypatch, route):
    viewer = open_pdf(window)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.No)
    leave(window, viewer, route)
    assert len(asked) == 1 and "disk full" in asked[0]
    assert readers(window) == [viewer] and window.isVisible()
    assert viewer._doc.pageCount() == 2  # still open and usable
    broken_storage()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    leave(window, viewer, route)  # the retry saves the place
    assert "page" in window.app.storage.load_reading_pos("Book", "book.pdf")
    assert not window.isVisible() if route == "quit" else readers(window) == []


@pytest.mark.parametrize("route", ROUTES)
def test_continuing_without_saving_leaves_the_reader(window, broken_storage, monkeypatch, route):
    viewer = open_pdf(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    leave(window, viewer, route)
    if route == "quit":
        assert not window.isVisible()
    else:
        assert readers(window) == []
    assert window.app.storage.load_reading_pos("Book", "book.pdf") == {}


def test_a_pdf_save_error_is_not_swallowed(window, broken_storage):
    viewer = open_pdf(window)
    with pytest.raises(OSError, match="disk full"):
        viewer.save_position()


@pytest.mark.parametrize("has_context", [False, True])
def test_without_a_handler_a_failed_save_keeps_the_component(qt, has_context):
    messages = []

    class Reader(QWidget):
        def __init__(self):
            super().__init__()
            self.ctx = SimpleNamespace(log=SimpleNamespace(emit=messages.append)) if has_context else None

        def on_replaced(self):
            raise OSError("Disk full")

    workspace = Workspace(lambda key: Reader() if key == "reader" else QWidget(), ["reader", "other"])
    pane = workspace.focused
    reader = pane.component
    try:
        if has_context:
            workspace.set_focused_component("other")
            assert messages == ["! Could not switch this pane: Disk full"]
        else:
            with pytest.raises(OSError, match="Disk full"):
                workspace.set_focused_component("other")
        assert pane.component is reader and pane.factory_key == "reader"
    finally:
        workspace.deleteLater()
        qt.processEvents()
