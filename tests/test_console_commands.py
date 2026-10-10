"""The desktop Console runs the terminal's command grammar on typed keys."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from .test_gui_reliability import qt, window, select
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


def test_console_reports_an_unclosed_quote_and_keeps_the_line(window, tmp_path, monkeypatch,
                                                              documents_folder):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    window.workspace.set_focused_component("console")
    component = window.workspace.focused.component
    component.cmd.setFocus()
    QTest.keyClicks(component.cmd, 'EXPORT Book "book.json')
    QTest.keyClick(component.cmd, Qt.Key_Home)  # Enter with the cursor anywhere
    QTest.keyClick(component.cmd, Qt.Key_Return)
    assert "! Unclosed quote" in component.out.toPlainText()
    assert set(tmp_path.iterdir()) == before
    assert list(documents_folder.iterdir()) == []
    # The line stays to be corrected, with the cursor at its end.
    assert component.cmd.text() == 'EXPORT Book "book.json'
    assert component.cmd.cursorPosition() == len(component.cmd.text())
    QTest.keyClicks(component.cmd, '"')
    QTest.keyClick(component.cmd, Qt.Key_Return)
    assert "Exported 'Book'" in component.out.toPlainText()
    assert (documents_folder / "book.json").is_file() and component.cmd.text() == ""
    assert not (tmp_path / "book.json").exists()  # not the working directory
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


def open_note(window, note="One"):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    window.ctx.changed.emit()
    select(notes, note)
    return notes, window.ctx.notes.buffers[("Book", note)]


def test_bodyless_note_add_keeps_a_clean_editor_and_its_undo(window, qt):
    notes, buffer = open_note(window)
    notes.editor.setFocus()
    notes.editor.moveCursor(QTextCursor.End)
    QTest.keyClicks(notes.editor, " and more")
    notes.save()
    path = window.app.storage._note_path("Book", "One")
    saved = path.read_bytes()
    log = console(window, "NOTE ADD One")
    assert "'One' already exists" in log
    qt.processEvents()
    assert path.read_bytes() == saved
    assert buffer.document.toPlainText() == "original and more"
    assert buffer.document.isUndoAvailable()


def test_bodyless_note_add_keeps_a_dirty_draft_saveable(window, qt):
    buffer = dirty_draft(window)
    console(window, "NOTE ADD One")
    qt.processEvents()
    assert window.app.storage.load_note("Book", "One") == "original"
    assert window.app.storage.load_note_draft("Book", "One")["content"] == "unsaved draft"
    assert buffer.document.toPlainText() == "unsaved draft"
    buffer.save()  # no false "changed on disk" conflict
    assert window.app.storage.load_note("Book", "One") == "unsaved draft"


def test_console_quoted_note_name(window):
    log = console(window, 'NOTE ADD "My Note" :: first line')
    assert "Created note 'My Note'" in log
    assert window.app.storage.load_note("Book", "My Note") == "first line"
    log = console(window, 'NOTE ADD "My Note"')
    assert "'My Note' already exists" in log
    assert window.app.storage.load_note("Book", "My Note") == "first line"


def test_console_reports_the_full_path_of_a_relative_file(window, tmp_path, monkeypatch,
                                                          documents_folder):
    started = tmp_path / "app folder"  # the packaged app starts in its own folder
    started.mkdir()
    monkeypatch.chdir(started)
    bundle = documents_folder / "book.json"
    assert f"Exported 'Book' -> {bundle}\n" in console(window, "EXPORT Book book.json")
    assert bundle.is_file()
    log = console(window, "IMPORT book.json as Copy")
    assert f"Imported module 'Copy' from {bundle} (fresh progress)" in log
    log = console(window, "IMPORT gone.json as Other")
    assert f"! Import file not found: {documents_folder / 'gone.json'}" in log
    assert list(started.iterdir()) == []  # never the app's own folder

def test_console_relative_paths_use_the_files_folder(window, tmp_path, monkeypatch,
                                                     documents_folder):
    started = tmp_path / "app folder"
    started.mkdir()
    monkeypatch.chdir(started)
    assert f"start in {documents_folder} (your Documents folder)" in console(window, "FOLDER")
    chosen = tmp_path / "USB stick"
    chosen.mkdir()
    log = console(window, f'FOLDER "{chosen}"')
    assert f"Relative file paths now start in {chosen}." in log
    assert f"Exported 'Book' -> {chosen / 'book.json'}" in console(window, "EXPORT Book book.json")
    assert (chosen / "book.json").is_file()
    assert window.app.config.files_folder == str(chosen)
    console(window, "FOLDER DEFAULT")
    console(window, "EXPORT Book book.json")
    assert (documents_folder / "book.json").is_file()
    assert list(started.iterdir()) == []
