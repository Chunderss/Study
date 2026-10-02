"""Notes the editor shows slightly differently must not count as unsaved.

QTextDocument drops a BOM and turns no-break spaces and Unicode line and
paragraph separators into plain ones. An untouched note with those characters
must open clean, close without a prompt and keep its exact bytes.
"""
import pytest
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox

from .test_gui_reliability import qt, window, select
from vocab.gui.main_window import MainWindow

SPECIAL = {
    "bom": "﻿# Title\n\nBody",
    "nbsp": "# Title\n\nno break space",
    "line-separator": "# Title\n\nfirst second",
    "paragraph-separator": "# Title\n\nfirst second",
}


@pytest.fixture(params=sorted(SPECIAL))
def special(request, window):
    text = SPECIAL[request.param]
    window.app.storage.save_note("Book", "Odd", text)
    window.ctx.changed.emit()
    return window, text


def path(window):
    return window.app.storage._note_path("Book", "Odd")


def test_untouched_note_opens_clean_and_closes_without_prompt(special, monkeypatch):
    window, text = special
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "Odd")
    buffer = notes._buffer
    assert not buffer.dirty and notes.dirty.text() == ""
    assert window.ctx.notes.dirty_buffers() == []
    assert window.app.storage.load_note_draft("Book", "Odd") is None
    assert notes.save()  # an explicit Save of an untouched note
    assert path(window).read_text(encoding="utf-8") == text
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args: asked.append(args) or QMessageBox.Save)
    assert window.close() is True and asked == []
    assert path(window).read_text(encoding="utf-8") == text


def test_outside_changes_still_reload_or_conflict(special):
    window, text = special
    buffer = window.ctx.notes.open("Book", "Odd")
    window.app.storage.save_note("Book", "Odd", text + "\n\nadded elsewhere")
    window.ctx.notes.open("Book", "Odd")  # opening again reloads a clean buffer
    assert buffer.document.toPlainText().endswith("added elsewhere") and not buffer.dirty
    buffer.document.setPlainText(buffer.document.toPlainText() + "\nmine")
    window.app.storage.save_note("Book", "Odd", "changed elsewhere again")
    with pytest.raises(ValueError, match="changed on disk"):
        buffer.save()


def test_edited_draft_is_recovered_and_saved(special, qt):
    window, text = special
    buffer = window.ctx.notes.open("Book", "Odd")
    buffer.document.setPlainText("edited\ntext")
    buffer.flush_recovery()
    reopened = MainWindow(root=window.app.paths.root)
    try:
        recovered = reopened.ctx.notes.buffers[("Book", "Odd")]
        assert recovered.recovered and recovered.dirty
        recovered.save()
        assert path(window).read_text(encoding="utf-8") == "edited\ntext"
        assert window.app.storage.load_note_draft("Book", "Odd") is None
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()
