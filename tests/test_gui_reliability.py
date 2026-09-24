"""Regression tests exercise real Qt key delivery, view lifetimes and drafts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox

from .test_markdown_preview import qt, notes
from vocab.gui.components import NotesComponent, VocabComponent
from vocab.gui.main_window import MainWindow


@pytest.fixture
def window(qt, tmp_path, monkeypatch):
    win = MainWindow(root=tmp_path)
    win.app.cmd_create("Book")
    win.app.cmd_use("Book")
    win.app.storage.save_note("Book", "One", "original")
    win.ctx.module_changed.emit("Book")
    win.show()
    qt.processEvents()
    yield win
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Discard)
    win.close()
    win.deleteLater()
    qt.processEvents()


def select(widget, note):
    widget.note_list.setCurrentItem(widget.note_list.findItems(note, Qt.MatchExactly)[0])


def test_typing_once_after_repeated_refresh_and_shared_panes(notes, qt):
    other = NotesComponent(notes.ctx)
    other.show()
    select(other, "Chapter")
    try:
        for _ in range(20):
            notes.ctx.changed.emit()
        notes.editor.selectAll()
        QTest.keyClicks(notes.editor, "one keystroke once")
        assert notes.editor.toPlainText() == "one keystroke once"
        assert other.editor.toPlainText() == "one keystroke once"
        assert notes.editor.document() is other.editor.document()
        QTest.qWait(160)
        assert other.markdown.preview.toPlainText() == "one keystroke once"
        other.save()
        assert not notes._buffer.dirty
    finally:
        other.deleteLater()
        qt.processEvents()


def test_clean_refresh_preserves_cursor_selection_and_undo(notes):
    notes.editor.moveCursor(QTextCursor.End)
    notes.editor.insertPlainText("x")
    notes.save()
    cursor = notes.editor.textCursor()
    cursor.setPosition(4)
    cursor.setPosition(8, QTextCursor.KeepAnchor)
    notes.editor.setTextCursor(cursor)
    notes.ctx.changed.emit()
    assert notes.editor.textCursor().selectedText() == cursor.selectedText()
    notes.editor.undo()
    assert notes.editor.toPlainText() == "# Original\n\nA note."


def test_switch_notes_and_modules_preserves_each_draft(notes):
    notes.editor.setPlainText("first draft")
    notes.app.storage.save_note("Book", "Second", "second original")
    notes.refresh()
    select(notes, "Second")
    notes.editor.setPlainText("second draft")
    select(notes, "Chapter")
    assert notes.editor.toPlainText() == "first draft"
    notes.app.cmd_create("Other")
    notes.app.cmd_use("Other")
    notes.ctx.module_changed.emit("Other")
    assert not notes.editor.isEnabled()
    notes.app.cmd_use("Book")
    notes.ctx.module_changed.emit("Book")
    select(notes, "Second")
    assert notes.editor.toPlainText() == "second draft"
    notes.save()
    assert notes.app.storage.load_note("Book", "Chapter") == "# Original\n\nA note."
    assert notes.app.storage.load_note("Book", "Second") == "second draft"


def test_module_switch_never_saves_draft_to_wrong_module(notes):
    notes.editor.setPlainText("Book draft")
    notes.app.cmd_create("Other")
    notes.app.cmd_use("Other")  # save before the view gets its change signal
    notes.save()
    assert notes.app.storage.load_note("Book", "Chapter") == "Book draft"
    assert notes.app.storage.note_names("Other") == []


def test_duplicate_new_note_does_not_overwrite(notes, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("Chapter.md", True))
    notes.editor.setPlainText("unsaved")
    notes._new()
    assert notes.app.storage.load_note("Book", "Chapter") == "# Original\n\nA note."
    assert notes.editor.toPlainText() == "unsaved"


def test_delete_clears_all_views_and_preview(notes, qt, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    other = NotesComponent(notes.ctx)
    select(other, "Chapter")
    notes.editor.setPlainText("unsaved")
    notes._delete()
    assert notes.editor.toPlainText() == other.editor.toPlainText() == ""
    assert notes.markdown.preview.toPlainText() == ""
    assert not notes.ctx.notes.dirty_buffers()
    other.deleteLater()
    qt.processEvents()


def test_failed_save_keeps_draft_and_blocks_close(window, monkeypatch):
    window.workspace.set_focused_component("notes")
    nc = window.workspace.focused.component
    select(nc, "One")
    nc.editor.setPlainText("draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    def fail(*args):
        raise OSError("Disk full")
    monkeypatch.setattr(window.app.storage, "save_note", fail)
    assert not window.close()
    assert window.isVisible()
    assert nc.editor.toPlainText() == "draft"
    assert nc._buffer.dirty


def test_close_saves_drafts_from_replaced_panes(window, monkeypatch):
    window.workspace.set_focused_component("notes")
    nc = window.workspace.focused.component
    select(nc, "One")
    nc.editor.setPlainText("draft survives pane")
    window.workspace.cycle_component()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Cancel)
    assert not window.close()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Save)
    assert window.close()
    assert window.app.storage.load_note("Book", "One") == "draft survives pane"


def test_external_edit_conflict_keeps_both_versions(notes, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: None)
    notes.editor.setPlainText("local draft")
    notes.app.storage.save_note("Book", "Chapter", "external edit")
    assert not notes.save()
    assert notes.editor.toPlainText() == "local draft"
    assert notes.app.storage.load_note("Book", "Chapter") == "external edit"


def test_hotkeys_do_not_repeat_or_leak_command_into_editor(window, qt):
    window.workspace.set_focused_component("notes")
    nc = window.workspace.focused.component
    select(nc, "One")
    nc.editor.setFocus()
    QTest.keyClick(nc.editor, Qt.Key_B, Qt.ControlModifier)
    QTest.keyPress(nc.editor, Qt.Key_R)
    for _ in range(5):
        qt.sendEvent(nc.editor, QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.NoModifier, "r", True))
    QTest.keyRelease(nc.editor, Qt.Key_R)
    assert nc.markdown._preview_panel.isHidden()
    assert nc.editor.toPlainText() == "original"
    QTest.keyClicks(nc.editor, "x")
    assert nc.editor.toPlainText().count("x") == 1


def test_hotkeys_do_not_hijack_dialog(window, qt):
    dialog = QDialog(window)
    line = QLineEdit(dialog)
    dialog.show()
    line.setFocus()
    QTest.keyClick(line, Qt.Key_B, Qt.ControlModifier)
    QTest.keyClicks(line, "v")
    assert line.text() == "v"
    assert len(window.workspace._panes) == 1
    dialog.close()


def test_lookup_keeps_event_loop_live_and_targets_original_module(window, qt, monkeypatch):
    from vocab.core.models import Sense
    release = threading.Event()
    started = threading.Event()
    def lookup(*a):
        started.set()
        assert release.wait(5)
        return [Sense("definition")], "test", ""
    monkeypatch.setattr(window.app, "_lookup_senses", lookup)
    vc = window.workspace.focused.component
    vc.add_input.setText("word")
    vc._add()
    try:
        for _ in range(100):
            if started.is_set(): break
            QTest.qWait(10)
        assert started.is_set()
        window.app.cmd_create("Other")
        window.app.cmd_use("Other")
        window.ctx.module_changed.emit("Other")
        QTest.keyClicks(vc.add_input, "next")
        assert vc.add_input.text() == "wordnext"
    finally:
        release.set()
    for _ in range(100):
        if not window.ctx.busy: break
        QTest.qWait(10)
    assert not window.ctx.busy
    assert window.app.storage.load_words("Book").has("word")
    assert not window.app.storage.load_words("Other").has("word")
    assert vc.add_input.text() == "wordnext"


def test_failed_add_retains_input(window):
    vc = window.workspace.focused.component
    vc.add_input.setText("word :: ")
    vc._add()
    assert vc.add_input.text() == "word :: "
    assert not window.app.storage.load_words("Book").words


def test_mechanical_feedback_waits_for_continue(window, qt):
    from vocab.gui.study_view import StudyView
    from vocab.study.session import StudySession
    from vocab.study.judge import KeywordJudge
    window.app.cmd_add("word", manual_def="a brief event")
    session = StudySession(window.app.storage, window.app.scheduler, ["Book"],
                           mechanical=True, judge=KeywordJudge())
    view = StudyView(session)
    view.show()
    view.focus_default()
    view.input.setText("brief event")
    QTest.keyClick(view.input, Qt.Key_Return)
    assert "reference:" in view.answer.text()
    assert "score" in view.feedback.text()
    assert session.reviewed == 1
    assert not view._ended
    view._continue()
    assert view._ended
    view.deleteLater()
    qt.processEvents()


def test_console_confirmation_works_without_terminal(window, monkeypatch):
    window.workspace.set_focused_component("console")
    console = window.workspace.focused.component
    monkeypatch.setattr("sys.stdin", None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    console.cmd.setText("DELETE Book")
    console._run()
    assert not window.app.storage.exists("Book")
    assert window.module_list.count() == 0


def test_ctrl_s_saves_focused_note(window, qt):
    window.workspace.set_focused_component("notes")
    nc = window.workspace.focused.component
    select(nc, "One")
    nc.editor.setFocus()
    qt.processEvents()
    nc.editor.setPlainText("saved with shortcut")
    QTest.keyClick(nc.editor, Qt.Key_S, Qt.ControlModifier)
    assert window.app.storage.load_note("Book", "One") == "saved with shortcut"


def test_ime_commit_is_inserted_once(notes, qt):
    from PySide6.QtGui import QInputMethodEvent
    notes.editor.clear()
    event = QInputMethodEvent()
    event.setCommitString("café 日本語")
    qt.sendEvent(notes.editor, event)
    notes.ctx.changed.emit()
    assert notes.editor.toPlainText() == "café 日本語"


def test_empty_lookup_preserves_input_and_draft(window, qt, monkeypatch):
    monkeypatch.setattr(window.app, "_lookup_senses", lambda *a: ([], "test", ""))
    vc = window.workspace.focused.component
    vc.add_input.setText("unknownword")
    vc._add()
    for _ in range(100):
        if not window.ctx.busy: break
        QTest.qWait(10)
    assert not window.ctx.busy
    assert vc.add_input.text() == "unknownword"
    assert not window.app.storage.load_words("Book").words
    assert vc.add_btn.isEnabled()
