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


def reopen(window, viewer):
    window.workspace.set_focused_component("documents")  # saves the place
    return window._open_viewer(viewer.path)


def mode(viewer):
    return viewer._view.zoomMode().name


def test_fit_width_and_custom_zoom_are_remembered(window, qt):
    make_pdf(window)
    path = str(window.app.paths.documents_dir("Book") / "book.pdf")
    viewer = window._open_viewer(path)
    assert mode(viewer) == "FitToWidth"
    viewer = reopen(window, viewer)
    assert window.app.storage.load_reading_pos("Book", "book.pdf") == {"page": 0, "zoom_mode": "fit_width"}
    window.resize(600, 700)  # a narrower pane: still fits its width
    settle(qt)
    assert mode(viewer) == "FitToWidth"
    viewer._zoom(1.25)
    factor = viewer._view.zoomFactor()
    viewer = reopen(window, viewer)
    assert mode(viewer) == "Custom" and viewer._view.zoomFactor() == pytest.approx(factor)
    viewer._fit()
    viewer = reopen(window, viewer)
    assert mode(viewer) == "FitToWidth"


@pytest.mark.parametrize("saved, factor", [({"page": 0, "zoom": 1.5}, 1.5), ({"zoom": 50}, 5.0),
                                           ({"zoom": 0.001}, 0.1)])
def test_older_records_and_extreme_zoom_open_as_custom(window, saved, factor):
    make_pdf(window)
    window.app.storage.save_reading_pos("Book", "book.pdf", saved)
    viewer = window._open_viewer(str(window.app.paths.documents_dir("Book") / "book.pdf"))
    assert mode(viewer) == "Custom" and viewer._view.zoomFactor() == pytest.approx(factor)


def test_zooming_in_from_fit_width_enlarges_the_page(window, qt):
    window.resize(1600, 900)
    make_pdf(window)
    viewer = window._open_viewer(str(window.app.paths.documents_dir("Book") / "book.pdf"))
    settle(qt)
    shown = viewer._effective_zoom()
    assert shown > 1.3  # a wide pane shows the page above 100%
    assert viewer._view.horizontalScrollBar().maximum() == 0  # it fits
    viewer._zoom(1.25)
    settle(qt)
    assert viewer._view.zoomFactor() == pytest.approx(shown * 1.25)
    assert viewer._view.horizontalScrollBar().maximum() > 0  # now wider than the pane
