"""A PDF opens at the requested page and keeps it once laid out."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractSlider

from .test_gui_reliability import qt, window  # noqa: F401
from .test_learning_gui import make_pdf
from vocab.gui.viewers import PdfViewer


def page(viewer):
    return viewer._view.pageNavigator().currentPage()


def settle(qt, ms=150):
    for _ in range(ms // 10):
        QTest.qWait(10)


def open_source(window, page_index=1):
    make_pdf(window)
    window._open_source("Book", {"filename": "book.pdf", "kind": "pdf", "page": page_index})
    viewer = window.workspace.focused.component
    assert isinstance(viewer, PdfViewer)
    return viewer


def test_a_source_page_survives_layout_resizing_and_hiding(window, qt):
    window.resize(1000, 700)
    assert window.app.storage.load_reading_pos("Book", "book.pdf") == {}  # never saved
    viewer = open_source(window)
    for delay in (20, 100, 500):
        settle(qt, delay)
        assert page(viewer) == 1, delay
    assert viewer._view.verticalScrollBar().value() > 0
    for width in (600, 1200):
        window.resize(width, 700)
        settle(qt)
        assert page(viewer) == 1, width
    pane = window.workspace.focused
    window.workspace.split_vertical()  # the new pane gets focus
    window.workspace.toggle_zoom()     # and hides the reader's pane
    settle(qt)
    window.workspace.toggle_zoom()
    window.workspace.focus_pane(pane)
    settle(qt)
    assert page(viewer) == 1
    assert viewer.source_location()["page"] == 1


@pytest.mark.parametrize("saved", [{"page": 1}, {"page": 1, "zoom": 1.0}])
def test_a_saved_page_survives_layout(window, qt, saved):
    make_pdf(window)
    window.app.storage.save_reading_pos("Book", "book.pdf", saved)
    viewer = window._open_viewer(str(window.app.paths.documents_dir("Book") / "book.pdf"))
    settle(qt, 300)
    assert page(viewer) == 1


def test_scrolling_away_is_never_undone(window, qt):
    viewer = open_source(window)
    settle(qt)
    viewer._view.verticalScrollBar().triggerAction(QAbstractSlider.SliderToMinimum)
    settle(qt)
    assert page(viewer) == 0
    window.resize(700, 600)
    settle(qt)
    assert page(viewer) == 0 and viewer.source_location()["page"] == 0
