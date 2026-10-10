"""The desktop Console runs the terminal's command grammar on typed keys."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from .test_gui_reliability import qt, window
from vocab.gui.components import ConsoleComponent


def console(window, line):
    """Type ``line`` into the Console's command field and press Enter."""
    if not isinstance(window.workspace.focused.component, ConsoleComponent):
        window.workspace.set_focused_component("console")
    component = window.workspace.focused.component
    component.cmd.setFocus()
    QTest.keyClicks(component.cmd, line)
    assert component.cmd.text() == line  # every key, backslashes and quotes included
    QTest.keyClick(component.cmd, Qt.Key_Return)
    return component.out.toPlainText()


def dirty_draft(window, note="One", text="unsaved draft"):
    buffer = window.ctx.notes.open("Book", note)
    buffer.document.setPlainText(text)
    buffer.flush_recovery()
    return buffer


def test_console_exports_to_an_unquoted_native_path(window, tmp_path):
    folder = tmp_path / "exports"
    folder.mkdir()
    path = str(folder) + "\\book.json"  # a separator on Windows, a name character elsewhere
    log = console(window, f"EXPORT Book {path}")
    assert f"Exported 'Book' -> {path}" in log
    assert os.path.exists(path)


def test_console_reports_an_unclosed_quote(window, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    log = console(window, 'EXPORT Book "book.json')
    assert "! Unclosed quote" in log
    assert set(tmp_path.iterdir()) == before
    assert "Current list: Book" in console(window, "WHERE")  # the Console carries on


def test_console_rejects_a_body_where_none_is_taken_and_keeps_the_draft(window):
    buffer = dirty_draft(window)
    studies = []
    window.ctx.study_requested.connect(studies.append)
    log = console(window, "NOTE DEL One :: oops")
    assert "'::' starts a definition or note text" in log
    assert window.app.storage.load_note("Book", "One") == "original"
    assert window.app.storage.load_note_draft("Book", "One")["content"] == "unsaved draft"
    assert ("Book", "One") in window.ctx.notes.buffers and buffer._active and buffer.dirty
    log = console(window, "STUDY Book :: oops")
    assert log.count("'::' starts a definition or note text") == 2
    assert studies == []


def test_console_multiword_terms(window):
    log = console(window, "ADD ice cream :: frozen dessert")
    assert "Added 'ice cream'" in log
    assert window.app.storage.load_words("Book").words["ice cream"].primary_definition() == \
        "frozen dessert"
    console(window, "DEL ice cream")
    assert window.app.storage.load_words("Book").words == {}
