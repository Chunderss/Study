"""A failed Discard at close must leave every editable note tracked."""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox

from .test_gui_reliability import qt, window, select


def test_partial_discard_failure_keeps_all_drafts_tracked_and_protected(window, monkeypatch):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "One")
    notes.editor.setPlainText("first draft")
    first = notes._buffer
    window.app.storage.save_note("Book", "Two", "saved two")
    second = window.ctx.notes.open("Book", "Two")
    second.document.setPlainText("second draft")
    first.flush_recovery()
    second.flush_recovery()
    storage = window.app.storage
    clear = storage.clear_note_draft
    def fail_second(module, note):
        if note == "Two":
            raise OSError("cannot remove the second copy")
        clear(module, note)
    monkeypatch.setattr(storage, "clear_note_draft", fail_second)
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    assert window.close() is False
    assert "cannot remove the second copy" in warnings[-1]
    # Nothing was dropped: both buffers are still tracked, and the copy that
    # was removed first has been written again.
    assert first._active and second._active
    assert {(b.module, b.note) for b in window.ctx.notes.dirty_buffers()} == {("Book", "One"), ("Book", "Two")}
    assert storage.load_note_draft("Book", "One")["content"] == "first draft"
    # Editing resumes with recovery, and the next close still asks.
    notes.editor.setPlainText("new work after the cancelled close")
    first.flush_recovery()
    assert storage.load_note_draft("Book", "One")["content"] == "new work after the cancelled close"
    monkeypatch.setattr(storage, "clear_note_draft", clear)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args[2]) or QMessageBox.Save)
    assert window.close() is True
    assert "Book/One" in asked[-1] and "Book/Two" in asked[-1]
    assert storage.load_note("Book", "One") == "new work after the cancelled close"
    assert storage.load_note_draft("Book", "One") is None
