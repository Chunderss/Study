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
    viewer._view.setZoomMode(viewer._view.ZoomMode.Custom)  # a deliberate 100%
    viewer._view.setZoomFactor(1.0)
    viewer = reopen(window, viewer)
    assert window.app.storage.load_reading_pos("Book", "book.pdf")["zoom_mode"] == "custom"
    assert mode(viewer) == "Custom" and viewer._view.zoomFactor() == 1.0


def test_an_older_record_of_fit_width_opens_fitted(window):
    make_pdf(window)
    # Older versions saved Fit width as the stale factor 1.0.
    window.app.storage.save_reading_pos("Book", "book.pdf", {"page": 0, "zoom": 1.0})
    viewer = window._open_viewer(str(window.app.paths.documents_dir("Book") / "book.pdf"))
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


def test_a_zoom_step_never_goes_the_wrong_way(window, monkeypatch):
    make_pdf(window)
    viewer = window._open_viewer(str(window.app.paths.documents_dir("Book") / "book.pdf"))
    for shown, step in ((6.0, 1.25), (0.05, 0.8)):  # Fit width beyond a limit
        monkeypatch.setattr(viewer, "_effective_zoom", lambda shown=shown: shown)
        viewer._zoom(step)
        assert mode(viewer) == "FitToWidth"  # no step possible: nothing changes
    viewer._zoom(0.8)  # from 0.05 back up is fine
    monkeypatch.setattr(viewer, "_effective_zoom", lambda: 6.0)
    viewer._zoom(0.8)
    assert mode(viewer) == "Custom" and viewer._view.zoomFactor() == pytest.approx(4.8)


def aligned(viewer, page_index=None):
    """Whether the view shows a page (the current one) from its top. The top
    is found by jumping there from the first or last page, both tall."""
    from PySide6.QtCore import QPointF
    bar, nav = viewer._view.verticalScrollBar(), viewer._view.pageNavigator()
    value = bar.value()
    page_index = nav.currentPage() if page_index is None else page_index
    nav.jump(0 if page_index > 1 else viewer._doc.pageCount() - 1, QPointF(0, 0))
    nav.jump(page_index, QPointF(0, 0))
    top = bar.value()
    bar.setValue(value)
    return value == top


def make_pdf_with_short_page(window, sizes=(792, 60, 792, 792)):
    """A small real PDF with variable page heights."""
    path = window.app.paths.documents_dir("Book") / "short.pdf"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [" + b" ".join(b"%d 0 R" % (3 + i) for i in range(len(sizes)))
               + b"] /Count %d >>" % len(sizes)]
    objects += [b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 %d] >>" % height for height in sizes]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    start = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, start)
    path.write_bytes(bytes(pdf))
    return path


@pytest.mark.parametrize("sizes, target", [((792, 60, 792, 792), 2),
                                          ((792, 60, 792), 2),
                                          ((792, 60, 60, 792), 3)])
def test_a_short_page_before_the_target_does_not_misalign_it(window, qt, sizes, target):
    window.resize(1000, 800)
    make_pdf_with_short_page(window, sizes)
    window._open_source("Book", {"filename": "short.pdf", "kind": "pdf", "page": target})
    viewer = window.workspace.focused.component
    settle(qt)
    assert viewer._doc.pageCount() == len(sizes) and aligned(viewer, target)
    window.resize(800, 800)
    settle(qt)
    assert aligned(viewer, target)
    viewer._zoom(1.25)
    settle(qt)
    assert aligned(viewer, target)
    # Reapplying a laid-out target should not add neighboring pages to history.
    jumps = []
    nav = viewer._view.pageNavigator()
    history = (nav.backAvailable(), nav.forwardAvailable())
    nav.jumped.connect(jumps.append)
    for _ in range(5):
        viewer._apply_pending_page()
    assert jumps == []
    assert (nav.backAvailable(), nav.forwardAvailable()) == history


def test_a_single_page_source_returns_to_its_top_and_stays_aligned(window, qt):
    window.resize(1000, 600)
    path = make_pdf_with_short_page(window, (792,))
    viewer = window._open_viewer(str(path))
    settle(qt)
    bar = viewer._view.verticalScrollBar()
    top = viewer._view.documentMargins().top()
    assert bar.maximum() > top + 20
    bar.setValue(bar.maximum())
    viewer.go_to({"page": 0})
    assert bar.value() == top
    window.resize(800, 600)
    settle(qt)
    assert bar.value() == top
    viewer._zoom(1.25)
    settle(qt)
    assert bar.value() == top and viewer.source_location()["page"] == 0


def test_the_requested_page_is_shown_from_its_top_after_relayout(window, qt):
    window.resize(1200, 700)
    viewer = open_source(window)
    settle(qt)
    assert page(viewer) == 1 and aligned(viewer)
    window.resize(1000, 700)
    settle(qt)
    assert page(viewer) == 1 and aligned(viewer)
    viewer._zoom(0.8)
    settle(qt)
    assert page(viewer) == 1 and aligned(viewer)


@pytest.mark.parametrize("choice", ["dismissed", "scroll_here"])
def test_the_scrollbar_menu_ends_the_pending_page_only_if_it_moves(window, qt, monkeypatch, choice):
    # The offscreen platform cannot show the menu (Windows' style has one), so
    # stand in for the scrollbar's own handling: "Scroll here" moves it
    # without a scrollbar action; a dismissed menu does nothing.
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QContextMenuEvent
    from PySide6.QtWidgets import QApplication
    viewer = open_source(window)
    settle(qt)
    bar = viewer._view.verticalScrollBar()
    handled = []

    def menu(event):
        handled.append(event.type())
        if choice == "scroll_here":
            bar.setValue(0)
        return True
    monkeypatch.setattr(bar, "event", menu)
    point = QPoint(2, 20)
    QApplication.sendEvent(bar, QContextMenuEvent(QContextMenuEvent.Mouse, point, bar.mapToGlobal(point)))
    assert handled  # the filter delivered the event to the scrollbar
    assert viewer._pending_page == (1 if choice == "dismissed" else None)
