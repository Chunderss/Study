"""Each reader pane keeps its own place and zoom, also when two panes show the
same document, and the document's saved place follows the reader used last."""
import json
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from .test_gui_reliability import qt, window  # noqa: F401
from .test_learning_gui import make_pdf
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import PdfViewer


def readers(window):
    return [pane.component for pane in window.workspace._panes
            if isinstance(pane.component, PdfViewer)]


def place(viewer):
    """(page, zoom) as the reader shows it; zoom is "fit" in Fit width mode."""
    view = viewer._view
    page = viewer._current_page()
    assert view.pageNavigator().currentPage() == page
    if view.zoomMode() == QPdfView.ZoomMode.FitToWidth:
        return page, "fit"
    return page, round(view.zoomFactor(), 4)


def set_place(viewer, page, zoom):
    viewer.go_to({"page": page})
    if zoom == "fit":
        viewer._fit()
    else:
        viewer._view.setZoomMode(QPdfView.ZoomMode.Custom)
        viewer._view.setZoomFactor(zoom)


def press(viewer, label):
    """Click one of the reader's buttons, which never take keyboard focus."""
    button = next(b for b in viewer.findChildren(QPushButton) if b.text() == label)
    QTest.mouseClick(button, Qt.LeftButton)


def wheel(viewer, steps=-1):
    """Turn the mouse wheel over the page, as over an unfocused pane."""
    viewport = viewer._view.viewport()
    center = QPointF(viewport.rect().center())
    event = QWheelEvent(center, QPointF(viewport.mapToGlobal(center.toPoint())), QPoint(),
                        QPoint(0, 120 * steps), Qt.NoButton, Qt.NoModifier,
                        Qt.NoScrollPhase, False)
    QApplication.sendEvent(viewport, event)


def saved(window):
    return window.app.storage.load_reading_pos("Book", "book.pdf")


@pytest.fixture
def reopen(qt):
    windows = []

    def open_window(root):
        reopened = MainWindow(root=root)
        windows.append(reopened)
        return reopened
    yield open_window
    for reopened in windows:
        reopened.close()
        reopened.deleteLater()
    qt.processEvents()


def test_pdf_panes_reopen_at_their_own_page_and_zoom(window, reopen):
    root = window.app.paths.root
    path = str(make_pdf(window))
    set_place(window._open_viewer(path), 1, 1.5625)
    window.workspace.split_vertical()
    set_place(window._open_viewer(path), 0, "fit")
    assert window.close()

    reopened = reopen(root)
    assert not reopened._session_warning
    assert [place(viewer) for viewer in readers(reopened)] == [(1, 1.5625), (0, "fit")]
    assert reopened.close()

    # Older workspace files have no position, and a damaged one is ignored
    # part by part: the document's saved place or zoom applies instead.
    saved = {"page": 1, "zoom_mode": "custom", "zoom": 2.0}
    window.app.storage.save_reading_pos("Book", "book.pdf", saved)
    cases = [
        (None, (1, 2.0)),
        ([1], (1, 2.0)),
        ({"page": "x", "zoom": "y"}, (1, 2.0)),
        ({"page": True, "zoom_mode": "custom", "zoom": True}, (1, 2.0)),
        ({"page": -1, "zoom_mode": "zoomed", "zoom": 1.5}, (1, 2.0)),
        ({"page": 0.0, "zoom_mode": "custom", "zoom": float("nan")}, (1, 2.0)),
        ({"zoom_mode": "custom", "zoom": 10 ** 400}, (1, 2.0)),
        ({"zoom_mode": "custom"}, (1, 2.0)),
        ({"page": 0, "zoom_mode": "custom", "zoom": "y"}, (0, 2.0)),
        ({"page": "x", "zoom_mode": "fit_width"}, (1, "fit")),
        ({"page": 0, "zoom_mode": "custom", "zoom": 1.25}, (0, 1.25)),
    ]
    panes = []
    for order, (position, _expected) in enumerate(cases):
        pane = {"component": "viewer", "module": "Book", "filename": "book.pdf"}
        if position is not None:
            pane["position"] = position
        panes.append({"pane": pane, "order": order})
    (root / "workspace.json").write_text(json.dumps({
        "schema": 1, "module": "Book", "sidebar": True,
        "workspace": {"tree": {"direction": "h", "sizes": [], "children": panes},
                      "focused": 0, "zoomed": False}}), encoding="utf-8")
    reopened = reopen(root)
    assert reopened._session_warning == ""
    assert [place(viewer) for viewer in readers(reopened)] == [expected for _, expected in cases]


def test_quitting_saves_the_reader_used_last_as_the_documents_place(window, reopen, qt):
    root = window.app.paths.root
    path = str(make_pdf(window))
    first = window._open_viewer(path)
    set_place(first, 1, 1.5)
    window.workspace.split_vertical()
    second = window._open_viewer(path)  # opened last, and keeps keyboard focus
    set_place(second, 0, "fit")
    focus = QApplication.focusWidget()
    press(first, "A+")  # the first pane, last in use
    assert QApplication.focusWidget() is focus
    expected = first.reading_position()
    assert window.close()
    assert saved(window) == expected  # not the second pane, last in layout order

    # Restored panes count as used only once you scroll, page or zoom in them.
    reopened = reopen(root)
    reopened.show()
    qt.processEvents()
    first, second = readers(reopened)
    wheel(second)
    expected = second.reading_position()
    assert reopened.close()
    assert saved(window) == expected
    reopened = reopen(root)
    assert reopened.close()  # nothing used: the saved place stays
    assert saved(window) == expected

    # A reader you leave saves its place. A reader not used since then keeps
    # its own place in its pane, but does not overwrite the document's.
    for used_after in (False, True):
        reopened = reopen(root)
        reopened.show()
        qt.processEvents()
        first, second = readers(reopened)
        press(first, "A−")
        set_place(second, 1, 2.5)
        press(second, "Fit width")
        left = second.reading_position()
        QTest.mouseClick(second._view.viewport(), Qt.LeftButton)
        assert reopened.workspace.focused.component is second
        reopened.workspace.close_focused()
        assert readers(reopened) == [first] and saved(window) == left
        if used_after:
            press(first, "A+")
        kept = first.reading_position()
        assert reopened.close()
        assert saved(window) == (kept if used_after else left)
        reopened = reopen(root)
        assert [reader.reading_position() for reader in readers(reopened)] == [kept]
        assert reopened.close()
        # Two readers again for the next round.
        reopened = reopen(root)
        reopened.workspace.split_vertical()
        reopened._open_viewer(path)
        assert reopened.close()


def test_deleting_a_module_saves_the_reader_used_last(window, monkeypatch):
    path = str(make_pdf(window))
    first = window._open_viewer(path)
    set_place(first, 1, 1.5)
    window.workspace.split_vertical()
    second = window._open_viewer(path)
    set_place(second, 0, "fit")
    press(first, "A+")
    expected = first.reading_position()

    def locked(self, target):
        raise PermissionError(32, "The process cannot access the file")
    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    window._delete_module()
    monkeypatch.undo()
    assert window.app.storage.exists("Book")  # nothing was deleted
    assert readers(window) == []
    assert saved(window) == expected
