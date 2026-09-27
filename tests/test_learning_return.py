"""A source visit returns to the Learning entry in the pane that opened it."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from .test_gui_reliability import qt, window
from .test_learning_gui import make_pdf
from vocab.core.learning import LearningStore
from vocab.gui.learning import LearningComponent
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import PdfViewer


def learning_entry(window, count=1):
    make_pdf(window)
    store = LearningStore(window.app.storage)
    captures = [store.save_capture(
        "Book", kind="question", prompt=f"Question {index}",
        quote="\n".join(f"Source line {line}" for line in range(100)),
        source={"kind": "pdf", "filename": "book.pdf", "page": 1})
        for index in range(count)]
    window.workspace.set_focused_component("learning")
    component = window.workspace.focused.component
    selected = captures[count // 2]
    for row in range(component.entries.count()):
        if component.entries.item(row).data(Qt.UserRole) == selected["id"]:
            component.entries.setCurrentRow(row)
            break
    return component, selected


def back_button(viewer, text="‹ Learning"):
    return next(button for button in viewer.findChildren(QPushButton) if button.text() == text)


def test_source_request_adds_capture_target_without_mutating_saved_source(window):
    component, item = learning_entry(window)
    window.ctx.source_requested.disconnect(window._open_source)
    requests = []
    window.ctx.source_requested.connect(lambda module, source: requests.append((module, source)))
    component.open_source()
    module, source = requests[0]
    assert module == "Book"
    assert source["capture_id"] == item["id"]
    assert source["quote"] == item["quote"]
    assert source["_learning_origin"] is component
    assert component.selected()["source"] == item["source"]
    assert LearningStore(window.app.storage).load("Book")[item["id"]]["source"] == item["source"]


def test_learning_back_returns_to_originating_pane_and_module(window):
    component, item = learning_entry(window)
    original_pane = window.workspace.focused
    window.workspace.split_vertical()
    window.workspace.set_focused_component("vocab")
    other_pane = window.workspace.focused
    other_component = other_pane.component

    # A delayed source action must still replace the Learning pane that sent it.
    component.open_source()
    viewer = original_pane.component
    assert isinstance(viewer, PdfViewer)
    assert viewer._view.pageNavigator().currentPage() == 1
    assert other_pane.component is other_component
    window.workspace.focus_pane(other_pane)
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    back_button(viewer).click()

    assert window.app.current_module == "Book"
    assert window.workspace.focused is original_pane
    assert isinstance(original_pane.component, LearningComponent)
    assert original_pane.component.selected()["id"] == item["id"]
    assert other_pane.component is other_component


def test_learning_return_preserves_list_and_passage_scroll(window, qt):
    component, item = learning_entry(window, count=40)
    qt.processEvents()
    component.entries.verticalScrollBar().setValue(10)
    component.details.verticalScrollBar().setValue(15)
    state = component.navigation_state()
    assert state["entries_scroll"] == 10
    assert state["details_scroll"] == 15
    component.open_source()
    back_button(window.workspace.focused.component).click()
    QTest.qWait(20)
    returned = window.workspace.focused.component
    assert returned.selected()["id"] == item["id"]
    assert returned.navigation_state() == state


def test_old_viewer_back_signal_cannot_replace_a_new_reader(window):
    component, item = learning_entry(window)
    component.open_source()
    pane = window.workspace.focused
    original = pane.component
    window._open_source("Book", {"kind": "pdf", "filename": "book.pdf", "page": 0})
    current = pane.component
    assert current is not original
    window._close_viewer(pane, original)
    assert pane.component is current
    back_button(current).click()
    assert pane.component.selected()["id"] == item["id"]


def test_delayed_source_from_replaced_learning_component_is_ignored(window):
    component, _item = learning_entry(window)
    window.ctx.source_requested.disconnect(window._open_source)
    requests = []
    window.ctx.source_requested.connect(lambda module, source: requests.append((module, source)))
    component.open_source()
    window.workspace.set_focused_component("vocab")
    replacement = window.workspace.focused.component
    window._open_source(*requests[0])
    assert window.workspace.focused.component is replacement


def test_learning_return_survives_workspace_restart_in_another_module(window, qt):
    component, item = learning_entry(window)
    component.open_source()
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    assert window.close()
    reopened = MainWindow(window.app.paths.root)
    reopened.show()
    qt.processEvents()
    try:
        viewer = reopened.workspace.focused.component
        assert isinstance(viewer, PdfViewer)
        assert reopened.app.current_module == "Other"
        back_button(viewer).click()
        assert reopened.app.current_module == "Book"
        assert reopened.workspace.focused.component.selected()["id"] == item["id"]
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()


def test_deleted_capture_can_still_return_to_learning(window):
    component, item = learning_entry(window)
    component.open_source()
    store = LearningStore(window.app.storage)
    store.delete("Book", item["id"], item["revision"])
    back_button(window.workspace.focused.component).click()
    returned = window.workspace.focused.component
    assert isinstance(returned, LearningComponent)
    assert returned.selected() is None
    assert returned.entries.count() == 0


def test_deleted_module_keeps_reader_open_when_learning_return_is_unavailable(window):
    component, _item = learning_entry(window)
    component.open_source()
    viewer = window.workspace.focused.component
    window.app.cmd_delete("Book")
    back_button(viewer).click()
    assert window.workspace.focused.component is viewer
    assert "Cannot return to Learning" in window.status.text()


def test_regular_reader_back_still_opens_documents(window):
    path = make_pdf(window)
    viewer = window._open_viewer(str(path))
    back_button(viewer, "‹ Documents").click()
    assert window.workspace.focused.factory_key == "documents"


def test_missing_source_keeps_learning_selection(window):
    component, item = learning_entry(window)
    (window.app.paths.documents_dir("Book") / "book.pdf").unlink()
    component.open_source()
    assert window.workspace.focused.component is component
    assert component.selected()["id"] == item["id"]
    assert "moved or deleted" in window.status.text()
