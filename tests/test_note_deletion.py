"""Deleting a note removes its draft everywhere, including orphans and aliases."""
import os

import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox

from .test_gui_reliability import qt, window, select
from vocab.gui.main_window import MainWindow


def dirty_draft(window, note="One", text="unsaved draft"):
    buffer = window.ctx.notes.open("Book", note)
    buffer.document.setPlainText(text)
    buffer.flush_recovery()
    return buffer


def run_console(window, line):
    window.workspace.set_focused_component("console")
    console = window.workspace.focused.component
    console.cmd.setText(line)
    console._run()


def test_del_removes_an_orphaned_draft_for_good(window, qt, monkeypatch):
    storage = window.app.storage
    dirty_draft(window)
    os.remove(storage._note_path("Book", "One"))  # the Markdown file vanished
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    window.ctx.changed.emit()
    select(notes, "One")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    notes._delete()
    assert ("Book", "One") not in window.ctx.notes.buffers
    assert storage.load_note_draft("Book", "One") is None
    assert "One" not in [notes.note_list.item(i).text() for i in range(notes.note_list.count())]
    reopened = MainWindow(root=window.app.paths.root)
    try:
        assert ("Book", "One") not in reopened.ctx.notes.buffers
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()


def test_failed_orphan_cleanup_keeps_the_draft(window, monkeypatch):
    storage = window.app.storage
    buffer = dirty_draft(window)
    os.remove(storage._note_path("Book", "One"))
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    window.ctx.changed.emit()
    select(notes, "One")
    def fail(*args):
        raise OSError("drafts folder is read-only")
    monkeypatch.setattr(storage, "clear_note_draft", fail)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes)
    logged = []
    window.ctx.log.connect(logged.append)
    notes._delete()
    assert any("read-only" in message for message in logged)
    assert ("Book", "One") in window.ctx.notes.buffers and buffer._active and buffer.dirty


def test_windows_case_alias_delete_discards_the_open_draft(window, monkeypatch):
    # Simulate Windows: existing names resolve case-insensitively.
    storage = window.app.storage
    def insensitive(original):
        def resolve(*args):
            path = original(*args)
            if not path.parent.exists():
                return path
            return next((p for p in path.parent.iterdir()
                         if p.name.casefold() == path.name.casefold()), path)
        return resolve
    monkeypatch.setattr(storage, "_note_path", insensitive(storage._note_path))
    monkeypatch.setattr(storage, "_draft_path", insensitive(storage._draft_path))
    buffer = dirty_draft(window, text="draft that should be deleted")
    run_console(window, "NOTE DEL one")
    assert not storage._note_path("Book", "One").exists()
    assert ("Book", "One") not in window.ctx.notes.buffers and not buffer._active
    buffer.flush_recovery()  # an old timer must not bring the copy back
    assert storage.load_note_draft("Book", "One") is None


def test_notes_differing_only_in_case_stay_distinct(window):
    storage = window.app.storage
    storage.save_note("Book", "one", "lower-case note")
    if not storage._note_path("Book", "One").read_text(encoding="utf-8") == "original":
        pytest.skip("case-insensitive filesystem")
    buffer = dirty_draft(window)  # "One"
    run_console(window, "NOTE DEL one")
    assert not storage._note_path("Book", "one").exists()
    assert storage.load_note("Book", "One") == "original"
    assert ("Book", "One") in window.ctx.notes.buffers and buffer._active and buffer.dirty
