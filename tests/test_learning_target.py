"""Learning's Edit, Source and Delete act only on the highlighted capture."""
import pytest
pytest.importorskip("PySide6")
from PySide6 import QtGui
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QPushButton

from .test_gui_reliability import qt, window  # noqa: F401
from vocab.core.learning import LearningStore
from vocab.gui.learning import CaptureDialog, ReviewDialog


@pytest.fixture
def learning(window, monkeypatch):
    store = LearningStore(window.app.storage)
    captures = {
        "due": store.save_capture("Book", kind="concept", prompt="Due concept", quote="Due passage"),
        "other": store.save_capture("Book", kind="question", prompt="Other <b>question</b>",
                                    quote="Other passage"),
    }
    window.workspace.set_focused_component("learning")
    pane = window.workspace.focused.component
    # Record each action instead of opening its dialog or reader.
    acted = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a: acted.append(("delete", text)) or QMessageBox.No)
    monkeypatch.setattr(CaptureDialog, "exec", lambda self: acted.append(("edit", self.item["id"])) or 0)
    window.ctx.source_requested.connect(lambda module, source: acted.append(("source", source["capture_id"])))
    return pane, store, captures, acted


def highlight(pane, item):
    for row in range(pane.entries.count()):
        if pane.entries.item(row).data(Qt.UserRole) == item["id"]:
            pane.entries.setCurrentRow(row)
            return pane.entries.item(row)


def buttons(pane, *titles):
    found = {b.text(): b for b in pane.findChildren(QPushButton)}
    return [found[title] for title in titles]


def assert_no_target(pane, acted):
    assert pane.selected() is None and pane.details.toPlainText() == ""
    targets = buttons(pane, "Edit", "Source", "Delete")
    assert not any(button.isEnabled() for button in targets)
    for button in targets:
        button.click()
    # Enter on the list calls edit() without its button.
    pane.edit()
    pane.open_source()
    pane.delete()
    assert acted == []


def test_cancelled_review_leaves_nothing_for_edit_source_or_delete(learning, monkeypatch):
    pane, store, captures, acted = learning
    highlight(pane, captures["other"])  # not the capture due for review
    assert all(button.isEnabled() for button in buttons(pane, "Edit", "Source", "Delete"))
    reviewed = []
    monkeypatch.setattr(ReviewDialog, "exec", lambda dialog: reviewed.append(dialog.item["id"]) or 0)
    pane.review()
    assert reviewed == [captures["due"]["id"]]
    assert pane.entries.currentItem() is None and not pane.entries.selectedItems()
    assert_no_target(pane, acted)
    assert set(store.load("Book")) == {captures["due"]["id"], captures["other"]["id"]}


@pytest.mark.parametrize("selected", ["due", "other"])
def test_refresh_during_recall_keeps_the_passage_hidden(window, learning, monkeypatch, selected):
    pane, store, captures, acted = learning
    highlight(pane, captures[selected])
    during = []

    def recall(dialog):
        window.ctx.changed.emit()           # e.g. a word lookup finished meanwhile
        window.ctx.learning_changed.emit()
        during.append((pane.selected(), pane.entries.selectedItems(), pane.details.toPlainText()))
        return 0
    monkeypatch.setattr(ReviewDialog, "exec", recall)
    pane.review()
    assert during == [(None, [], "")]
    assert_no_target(pane, acted)


def test_a_current_row_without_highlight_is_not_a_target(window, learning, qt):
    pane, store, captures, acted = learning
    first = pane.entries.item(0)  # the open question sorts first
    assert first.data(Qt.UserRole) == captures["other"]["id"]
    # Opening Learning focuses its list. A list taking keyboard focus makes
    # its first row current without highlighting it, and nothing draws it.
    window.activateWindow()
    pane.entries.setFocus(Qt.TabFocusReason)
    qt.processEvents()
    assert qt.focusWidget() is pane.entries and not pane.entries.selectedItems()
    assert_no_target(pane, acted)
    # The first arrow key highlights the first capture, not the second.
    QTest.keyClick(pane.entries, Qt.Key_Down)
    assert pane.entries.selectedItems() == [first]
    assert pane.selected()["id"] == captures["other"]["id"]
    # Ctrl+click on the highlighted row removes its highlight; it stays current.
    QTest.mouseClick(pane.entries.viewport(), Qt.LeftButton, Qt.ControlModifier,
                     pane.entries.visualItemRect(first).center())
    assert pane.entries.currentItem() is first and not first.isSelected()
    assert_no_target(pane, acted)


def test_delete_names_the_highlighted_capture(learning, monkeypatch):
    pane, store, captures, acted = learning
    highlight(pane, captures["other"])
    for button in buttons(pane, "Edit", "Source"):
        button.click()
    assert acted == [("edit", captures["other"]["id"]), ("source", captures["other"]["id"])]
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a: asked.append(text) or QMessageBox.Yes)
    buttons(pane, "Delete")[0].click()
    assert len(asked) == 1 and "Other <b>question</b>" in asked[0] and "Due concept" not in asked[0]
    assert not QtGui.Qt.mightBeRichText(asked[0])  # the prompt shows as typed, tags and all
    assert set(store.load("Book")) == {captures["due"]["id"]}
