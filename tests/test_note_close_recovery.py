"""Closing finishes recovery work, so outdated drafts are not "recovered"."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox

from .test_gui_reliability import qt, window, select
from vocab.gui.main_window import MainWindow


def edit_then_undo(window):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "One")
    notes.editor.setPlainText("originalX")
    notes._buffer.flush_recovery()  # the recovery copy now holds "originalX"
    notes.editor.setPlainText("original")  # undone; its timer has not fired yet
    return notes._buffer


def test_undo_then_immediate_close_leaves_no_outdated_draft(window, qt):
    buffer = edit_then_undo(window)
    assert buffer._timer.isActive() and not buffer.dirty
    assert window.close() is True
    storage = window.app.storage
    assert storage.load_note_draft("Book", "One") is None
    reopened = MainWindow(root=window.app.paths.root)
    try:
        assert reopened.ctx.notes.dirty_buffers() == []
        assert storage.load_note("Book", "One") == "original"
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()


def test_failed_recovery_cleanup_asks_before_quitting(window, monkeypatch):
    buffer = edit_then_undo(window)
    storage = window.app.storage
    clear = storage.clear_note_draft
    def fail(*args):
        raise OSError("drafts folder is read-only")
    monkeypatch.setattr(storage, "clear_note_draft", fail)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.No)
    assert window.close() is False
    assert "Book/One: drafts folder is read-only" in asked[-1]
    assert ("Book", "One") in window.ctx.notes.buffers and buffer._active  # still tracked
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    assert window.close() is True  # an explicit "quit anyway"
    monkeypatch.setattr(storage, "clear_note_draft", clear)
