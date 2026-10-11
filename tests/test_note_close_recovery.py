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


def test_declining_to_quit_never_leaves_discarded_notes_untracked(window, monkeypatch):
    # "Two" has an outdated copy whose cleanup fails; "One" has unsaved changes.
    storage = window.app.storage
    storage.save_note("Book", "Two", "saved two")
    two = window.ctx.notes.open("Book", "Two")
    two.document.setPlainText("saved twoX")
    two.flush_recovery()
    two.document.setPlainText("saved two")  # undone; cleanup still pending
    one = window.ctx.notes.open("Book", "One")
    one.document.setPlainText("unsaved work")
    one.flush_recovery()
    clear = storage.clear_note_draft
    def fail_two(module, note):
        if note == "Two":
            raise OSError("cannot remove the outdated copy")
        clear(module, note)
    monkeypatch.setattr(storage, "clear_note_draft", fail_two)
    answers = {"Unsaved notes": QMessageBox.Discard, "Recovery copy not updated": QMessageBox.No}
    monkeypatch.setattr(QMessageBox, "question", lambda parent, title, *rest: answers[title])
    assert window.close() is False
    # Nothing was discarded: "One" is still tracked and backed up.
    assert one._active and one in window.ctx.notes.dirty_buffers()
    assert storage.load_note_draft("Book", "One")["content"] == "unsaved work"
    monkeypatch.setattr(storage, "clear_note_draft", clear)


def open_draft(window, module, note, text):
    window.app.storage.save_note(module, note, f"{note} on disk")
    buffer = window.ctx.notes.open(module, note)
    buffer.document.setPlainText(text)
    buffer.flush_recovery()
    return buffer


def test_close_save_tries_every_note_and_lists_each_failure(window, monkeypatch):
    storage = window.app.storage
    window.app.cmd_create("Other")
    a = open_draft(window, "Book", "A", "draft A")    # changed on disk: conflict
    b = open_draft(window, "Other", "B", "draft B")   # saves
    c = open_draft(window, "Book", "C", "draft C")    # write error
    storage.save_note("Book", "A", "A changed elsewhere")
    save_note = storage.save_note
    def fail_c(module, note, *args, **kwargs):
        if note == "C":
            raise OSError("disk full")
        return save_note(module, note, *args, **kwargs)
    monkeypatch.setattr(storage, "save_note", fail_c)
    warnings = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Save)
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda parent, title, text, *rest: warnings.append((title, text)))
    assert window.close() is False and window.isVisible()
    # B, after the conflict, was saved and its recovery copy removed.
    assert storage.load_note("Other", "B") == "draft B"
    assert storage.load_note_draft("Other", "B") is None and not b.dirty
    assert storage.load_note("Book", "A") == "A changed elsewhere"  # never overwritten
    for buffer, text in ((a, "draft A"), (c, "draft C")):
        assert buffer.dirty and buffer._active and buffer.document.toPlainText() == text
        assert storage.load_note_draft("Book", buffer.note)["content"] == text
    assert len(warnings) == 1
    title, text = warnings[0]
    assert title == "Could not save notes"
    assert "• Book/A: changed on disk." in text and "• Book/C: disk full" in text
    assert "Other/B" not in text and text.endswith("The other note was saved.")
    # Closing again offers only the notes still unsaved.
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *rest: asked.append(text) or QMessageBox.Cancel)
    assert window.close() is False
    assert "Book/A" in asked[0] and "Book/C" in asked[0] and "Other/B" not in asked[0]
    monkeypatch.setattr(storage, "save_note", save_note)
