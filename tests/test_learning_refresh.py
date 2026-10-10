"""The Learning pane keeps its place when something unrelated changes."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtTest import QTest

from .test_gui_reliability import qt, window  # noqa: F401
from vocab.core.learning import LearningStore


def settle():
    for _ in range(5):
        QTest.qWait(10)


@pytest.fixture
def learning(window):
    store = LearningStore(window.app.storage)
    quote = "\n".join(f"Line {n} of a long passage." for n in range(120))
    ids = [store.save_capture("Book", kind="question", prompt=f"Question {n}", quote=quote)["id"]
           for n in range(60)]
    window.resize(900, 500)
    window.workspace.set_focused_component("learning")
    pane = window.workspace.focused.component
    settle()
    pane.entries.setCurrentRow(30)
    settle()
    pane.entries.verticalScrollBar().setValue(pane.entries.verticalScrollBar().maximum() // 2)
    pane.details.verticalScrollBar().setValue(pane.details.verticalScrollBar().maximum() // 2)
    return pane, store, ids


def place(pane):
    selected = pane.selected()
    return (selected["id"] if selected else None, pane.entries.verticalScrollBar().value(),
            pane.details.verticalScrollBar().value())


def test_unrelated_changes_keep_the_selection_and_both_scroll_positions(window, learning):
    pane, store, _ = learning
    before = place(pane)
    assert before[1] > 0 and before[2] > 0
    window.ctx.changed.emit()               # e.g. Ctrl+S on a note in another pane
    settle()
    assert place(pane) == before
    window.app.storage.save_note("Book", "One", "edited")
    window.ctx.changed.emit()
    window.ctx.learning_changed.emit()      # another capture saved or reviewed
    settle()
    assert place(pane) == before


def test_removing_the_selected_capture_keeps_the_list_position(window, learning):
    pane, store, _ = learning
    _, entries, _ = place(pane)
    store.delete("Book", pane.selected()["id"], pane.selected()["revision"])
    window.ctx.learning_changed.emit()
    settle()
    assert pane.selected() is None and pane.entries.verticalScrollBar().value() == entries


def test_another_module_starts_at_the_top(window, learning):
    pane, store, _ = learning
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    window.ctx.changed.emit()
    settle()
    assert place(pane) == (None, 0, 0)
