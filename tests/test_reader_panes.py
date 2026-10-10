"""Each reader pane keeps its own place and zoom, also when two panes show the
same document, and the document's saved place follows the reader used last."""
import json

import pytest
pytest.importorskip("PySide6")
from PySide6.QtPdfWidgets import QPdfView

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
