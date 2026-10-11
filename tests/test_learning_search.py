"""Search and filter the Learning list from the keyboard. Edit, Source and
Delete still act only on the highlighted capture, and refreshes, ‹ Learning
and a restart keep the filters and the list's place."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from .test_gui_reliability import qt, window, select  # noqa: F401
from vocab.core.learning import LearningStore
from vocab.gui.learning import CaptureDialog, LearningComponent
from vocab.gui.main_window import MainWindow


def settle():
    for _ in range(5):
        QTest.qWait(10)


def type_keys(native, text):
    """Real key events, delivered through the window as the platform does."""
    for ch in text:
        if ch == " ":
            QTest.keyClick(native, Qt.Key_Space)
        else:
            QTest.keyClick(native, getattr(Qt, f"Key_{ch.upper()}"),
                           Qt.ShiftModifier if ch.isupper() else Qt.NoModifier)


def shown(pane):
    return [pane.items[pane.entries.item(row).data(Qt.UserRole)]["prompt"]
            for row in range(pane.entries.count())]


def highlight(pane, item_id):
    for row in range(pane.entries.count()):
        if pane.entries.item(row).data(Qt.UserRole) == item_id:
            pane.entries.setCurrentRow(row)
            return
    raise AssertionError("capture not shown")


@pytest.fixture
def learning(window, monkeypatch):
    store = LearningStore(window.app.storage)
    add = lambda prompt, **kw: store.save_capture("Book", prompt=prompt, **{
        "kind": "concept", "quote": "A plain passage.", **kw})
    made = {
        "cell": add("Why do cells divide?", quote="Mitochondria supply the energy.",
                    source={"kind": "epub", "filename": "biology.epub", "chapter": 2}),
        "cafe": add("What is a Café?", kind="question", explanation="Ask about the RÉSUMÉ.",
                    source={"kind": "epub", "filename": "biology.epub", "chapter": 0}),
        "page": add("Page question", kind="question", resolved=True,
                    source={"kind": "pdf", "filename": "Atlas.pdf", "page": 4}),
        "manual": add("Pasted concept", quote="Energy flows."),
    }
    # Reviewed, so it is not due until tomorrow.
    made["manual"] = store.review("Book", made["manual"]["id"], made["manual"]["revision"],
                                  "It flows.", "good")
    window.workspace.set_focused_component("learning")
    pane = window.workspace.focused.component
    acted = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a: acted.append(("delete", text)) or QMessageBox.No)
    monkeypatch.setattr(CaptureDialog, "exec", lambda self: acted.append(("edit", self.item["id"])) or 0)
    window.ctx.source_requested.connect(lambda module, source: acted.append(("source", source["capture_id"])))
    window.activateWindow()
    pane.entries.setFocus()
    QApplication.processEvents()
    return pane, store, made, acted


def assert_no_target(pane, acted):
    assert pane.selected() is None and pane.details.toPlainText() == ""
    found = {b.text(): b for b in pane.findChildren(QPushButton)}
    targets = [found[t] for t in ("Edit", "Source", "Delete")]
    assert not any(button.isEnabled() for button in targets)
    for button in targets:
        button.click()
    pane.edit(); pane.open_source(); pane.delete()
    assert acted == []


def test_ctrl_f_searches_question_passage_and_notes(window, learning):
    pane, store, made, acted = learning
    native = window.windowHandle()
    assert "Capture a passage" in pane.summary.text() and not pane.clear_filters.isEnabled()
    QTest.keyClick(native, Qt.Key_F, Qt.ControlModifier)
    assert QApplication.focusWidget() is pane.search
    type_keys(native, "mitochondria")                    # in a passage only
    assert shown(pane) == ["Why do cells divide?"]
    assert pane.summary.text().endswith("Showing 1 of 4 captures.")
    QTest.keyClick(native, Qt.Key_F, Qt.ControlModifier)  # selects the search text
    type_keys(native, "resume")                          # in notes; accents and case optional
    assert shown(pane) == ["What is a Café?"]
    QTest.keyClick(native, Qt.Key_A, Qt.ControlModifier)
    type_keys(native, "CAFE what")                       # every word, in any field
    assert shown(pane) == ["What is a Café?"]
    type_keys(native, " zebra")
    assert shown(pane) == []
    assert pane.summary.text().endswith("No captures match. Clear filters to show all 4.")
    QTest.keyClick(native, Qt.Key_Escape)                # clears the search
    assert pane.search.text() == "" and len(shown(pane)) == 4
    assert QApplication.focusWidget() is pane.search and acted == []
    # F1 lists the key under Learning.
    assert ("Ctrl+F", "Search captures", "Learning") in [
        (direct[0][2], label, category) for _leader, direct, label, category, _action
        in window._keymap() if direct]


def test_ctrl_f_searches_only_in_the_focused_learning_pane(window, learning):
    first = learning[0]
    native = window.windowHandle()
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    QTest.keyClick(native, Qt.Key_V)                      # a second Learning pane
    second = window.workspace.focused.component
    assert isinstance(second, LearningComponent) and second is not first
    for pane, other in ((second, first), (first, second)):
        pane.entries.setFocus()
        QApplication.processEvents()
        QTest.keyClick(native, Qt.Key_F, Qt.ControlModifier)
        assert QApplication.focusWidget() is pane.search
        type_keys(native, "energy")
        assert pane.entries.count() == 2 and other.entries.count() == 4
        QTest.keyClick(native, Qt.Key_Escape)
    # Elsewhere, Ctrl+F is not Learning's.
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "One")
    notes.editor.setFocus()
    QApplication.processEvents()
    QTest.keyClick(native, Qt.Key_F, Qt.ControlModifier)
    assert QApplication.focusWidget() is notes.editor and notes.editor.toPlainText() == "original"


def test_source_and_status_filters_by_keyboard(window, learning):
    pane, store, made, acted = learning
    native = window.windowHandle()
    sources = [pane.source_filter.itemText(i) for i in range(pane.source_filter.count())]
    assert sources == ["All sources", "Atlas.pdf", "Atlas.pdf · page 5", "biology.epub",
                       "biology.epub · chapter 1", "biology.epub · chapter 3", "Manual captures"]
    pane.focus_search()
    QTest.keyClick(native, Qt.Key_Tab)
    assert QApplication.focusWidget() is pane.source_filter
    for _ in range(3):
        QTest.keyClick(native, Qt.Key_Down)              # biology.epub
    assert sorted(shown(pane)) == ["What is a Café?", "Why do cells divide?"]
    QTest.keyClick(native, Qt.Key_Down)                  # its first chapter
    assert shown(pane) == ["What is a Café?"]
    QTest.keyClick(native, Qt.Key_End)                   # Manual captures
    assert shown(pane) == ["Pasted concept"]
    QTest.keyClick(native, Qt.Key_Home)
    QTest.keyClick(native, Qt.Key_Tab)
    assert QApplication.focusWidget() is pane.status_filter
    expected = {"Open questions": ["What is a Café?"], "Resolved questions": ["Page question"],
                "All questions": ["What is a Café?", "Page question"],
                "Concepts due now": ["Why do cells divide?"],
                "Concepts not due yet": ["Pasted concept"],
                "All concepts": ["Why do cells divide?", "Pasted concept"]}
    for _ in expected:
        QTest.keyClick(native, Qt.Key_Down)
        assert sorted(shown(pane)) == sorted(expected[pane.status_filter.currentText()])
    assert pane.status_filter.currentText() == "All concepts"
    QTest.keyClick(native, Qt.Key_Tab)
    assert QApplication.focusWidget() is pane.clear_filters
    QTest.keyClick(native, Qt.Key_Tab)
    assert QApplication.focusWidget() is pane.entries
    QTest.keyClick(native, Qt.Key_Backtab)
    QTest.keyClick(native, Qt.Key_Space)                 # Clear filters
    assert len(shown(pane)) == 4 and pane.status_filter.currentText() == "All captures"
    assert pane.source_filter.currentText() == "All sources" and not pane.clear_filters.isEnabled()
    assert QApplication.focusWidget() is pane.search
    assert acted == []


def test_typing_continues_in_the_search_box_while_the_list_refreshes(window, learning):
    pane, store, made, acted = learning
    native = window.windowHandle()
    QTest.keyClick(native, Qt.Key_F, Qt.ControlModifier)
    type_keys(native, "ener")
    window.ctx.changed.emit()             # e.g. a word lookup finished meanwhile
    window.ctx.learning_changed.emit()
    QApplication.processEvents()
    type_keys(native, "gy")
    assert QApplication.focusWidget() is pane.search and pane.search.text() == "energy"
    assert sorted(shown(pane)) == ["Pasted concept", "Why do cells divide?"]


def test_a_filtered_out_capture_is_no_longer_a_target(window, learning):
    pane, store, made, acted = learning
    native = window.windowHandle()
    highlight(pane, made["cell"]["id"])
    pane.focus_search()
    type_keys(native, "energy")                          # still shown: still highlighted
    assert pane.selected()["id"] == made["cell"]["id"]
    assert "Mitochondria" in pane.details.toPlainText()
    type_keys(native, " flows")                          # filtered out
    assert shown(pane) == ["Pasted concept"]
    assert_no_target(pane, acted)
    QTest.keyClick(native, Qt.Key_Escape)                # shown again, not highlighted again
    assert len(shown(pane)) == 4
    assert_no_target(pane, acted)
    QTest.keyClick(native, Qt.Key_Return)                # nothing to edit; on to the list
    assert QApplication.focusWidget() is pane.entries and acted == []


def test_down_moves_from_search_to_the_first_match_and_enter_edits_it(window, learning):
    pane, store, made, acted = learning
    native = window.windowHandle()
    QTest.keyClick(native, Qt.Key_F, Qt.ControlModifier)
    type_keys(native, "plain passage")
    assert shown(pane) == ["What is a Café?", "Page question"]
    highlight(pane, made["page"]["id"])                  # an already highlighted match stays
    pane.focus_search()
    QTest.keyClick(native, Qt.Key_Down)
    assert QApplication.focusWidget() is pane.entries
    assert pane.selected()["id"] == made["page"]["id"]
    QTest.keyClick(native, Qt.Key_Return)
    assert acted == [("edit", made["page"]["id"])]
    pane.entries.setCurrentItem(None)
    pane.focus_search()
    QTest.keyClick(native, Qt.Key_Down)                  # nothing highlighted: the first match
    assert pane.selected()["id"] == made["cafe"]["id"]


def many_captures(window, source=None):
    """60 open questions with a long passage, which "long passage" finds, and 60 others."""
    store = LearningStore(window.app.storage)
    quote = "\n".join(f"Line {n} of a long passage." for n in range(120))
    for n in range(60):
        store.save_capture("Book", kind="question", prompt=f"Match {n}", quote=quote, source=source)
        store.save_capture("Book", kind="question", prompt=f"Other {n}", quote="short", source=source)
    return store


def filtered_place(window, pane):
    """Search, scroll and highlight half-way down the filtered list."""
    pane.focus_search()
    QTest.keyClicks(pane.search, "long passage")
    pane.status_filter.setCurrentIndex(pane.status_filter.findText("Open questions"))
    assert len(shown(pane)) == 60
    pane.entries.setCurrentRow(30)
    settle()
    pane.entries.verticalScrollBar().setValue(pane.entries.verticalScrollBar().maximum() // 2)
    pane.details.verticalScrollBar().setValue(pane.details.verticalScrollBar().maximum() // 2)
    return place(pane)


def place(pane):
    selected = pane.selected()
    return (pane.search.text(), pane.source_filter.currentText(), pane.status_filter.currentText(),
            len(shown(pane)), selected["id"] if selected else None,
            pane.entries.verticalScrollBar().value(), pane.details.verticalScrollBar().value())


def test_refresh_keeps_filters_highlight_and_scroll(window):
    store = many_captures(window)
    window.resize(900, 500)
    window.workspace.set_focused_component("learning")
    pane = window.workspace.focused.component
    settle()
    before = filtered_place(window, pane)
    assert before[5] > 0 and before[6] > 0
    rebuilt = []
    pane.source_filter.model().modelReset.connect(lambda: rebuilt.append(True))
    pane.source_filter.model().rowsRemoved.connect(lambda *a: rebuilt.append(True))
    window.ctx.changed.emit()
    window.ctx.learning_changed.emit()
    settle()
    assert place(pane) == before
    assert pane.search.isEnabled() and pane.source_filter.isEnabled() and pane.clear_filters.isEnabled()
    assert rebuilt == []                  # an open source menu stays open
    # Saved elsewhere so it no longer matches: it leaves the list and the target.
    item = pane.selected()
    store.save_capture("Book", kind="question", prompt=item["prompt"], quote="short",
                       item_id=item["id"], revision=item["revision"])
    window.ctx.learning_changed.emit()
    settle()
    assert pane.selected() is None and len(shown(pane)) == 59
    # Another module starts unfiltered.
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    assert pane.search.text() == "" and pane.status_filter.currentText() == "All captures"
    assert pane.source_filter.count() == 1 and pane.summary.text().endswith("revisit.")


def test_source_return_and_restart_restore_filters_highlight_and_scroll(window, qt):
    from .test_learning_gui import make_pdf
    make_pdf(window)
    many_captures(window, source={"kind": "pdf", "filename": "book.pdf", "page": 1})
    window.workspace.set_focused_component("learning")
    pane = window.workspace.focused.component
    settle()
    pane.source_filter.setCurrentIndex(pane.source_filter.findText("book.pdf · page 2"))
    before = filtered_place(window, pane)
    assert before[1] == "book.pdf · page 2" and before[5] > 0 and before[6] > 0
    pane.open_source()
    viewer = window.workspace.focused.component
    next(b for b in viewer.findChildren(QPushButton) if b.text() == "‹ Learning").click()
    settle()
    assert place(window.workspace.focused.component) == before
    assert window.close()
    reopened = MainWindow(window.app.paths.root)
    reopened.show()
    settle()
    try:
        assert place(reopened.workspace.focused.component) == before
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()


def test_review_takes_the_next_due_concept_whatever_the_filter(window, learning, monkeypatch):
    from vocab.gui.learning import ReviewDialog
    pane, store, made, acted = learning
    pane.status_filter.setCurrentIndex(pane.status_filter.findText("Open questions"))
    pane.entries.setCurrentRow(0)
    reviewed = []
    monkeypatch.setattr(ReviewDialog, "exec", lambda dialog: reviewed.append(
        (dialog.item["id"], pane.selected(), pane.details.toPlainText())) or 0)
    pane.review()
    assert reviewed == [(made["cell"]["id"], None, "")]
    assert pane.status_filter.currentText() == "Open questions"


def test_a_capture_saved_where_the_filters_hide_it_is_reported(window, learning, monkeypatch):
    pane, store, made, acted = learning
    logged = []
    window.ctx.log.connect(logged.append)
    def fill(dialog, prompt):
        dialog.prompt.setText(prompt)
        if not dialog.item:
            dialog.quote.setPlainText("A new passage.")
        dialog.save()
        return 1
    pane.focus_search()
    QTest.keyClicks(pane.search, "energy")
    monkeypatch.setattr(CaptureDialog, "exec", lambda dialog: fill(dialog, "About energy"))
    pane.capture()                                       # matches: listed, nothing said
    assert "About energy" in shown(pane) and logged == []
    monkeypatch.setattr(CaptureDialog, "exec", lambda dialog: fill(dialog, "Unrelated"))
    pane.capture()                                       # hidden: says so
    assert "Unrelated" not in shown(pane) and len(pane.items) == 6
    assert logged == ["Capture saved. The current search or filters hide it; Clear filters shows it."]
    assert pane.summary.text().endswith("Showing 3 of 6 captures.")
    highlight(pane, next(key for key, item in pane.items.items() if item["prompt"] == "About energy"))
    monkeypatch.setattr(CaptureDialog, "exec", lambda dialog: fill(dialog, "Renamed"))
    pane.edit()                                          # edited out of the search
    assert len(logged) == 2 and pane.selected() is None and "Renamed" not in shown(pane)
