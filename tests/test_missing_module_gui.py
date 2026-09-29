"""A current module removed outside the app is deselected, not kept broken."""
import sys

import pytest
pytest.importorskip("PySide6")
from PySide6.QtTest import QTest

from .test_gui_reliability import qt, window, select
from vocab.cli.app import App
from vocab.gui.components import NotesComponent, VocabComponent
from vocab.gui.study_view import StudyView


@pytest.fixture
def slot_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(error))
    return errors


def settle(window, qt):
    window.module_list.setFocus()
    qt.processEvents()
    QTest.qWait(20)


def test_external_delete_deselects_the_module_and_keeps_drafts(window, qt, slot_errors):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    assert isinstance(notes, NotesComponent)
    select(notes, "One")
    notes.editor.setPlainText("unsaved draft")
    logged = []
    window.ctx.log.connect(logged.append)
    App(window.app.paths.root).cmd_delete("Book")
    for _ in range(3):
        settle(window, qt)
    assert window.app.current_module is None
    assert window.status.text().startswith("module: —")
    assert window.module_list.count() == 0
    assert len([m for m in logged if "'Book' is no longer available" in m]) == 1
    # The draft keeps its own module; nothing retargets it to another module.
    assert [(b.module, b.note) for b in window.ctx.notes.dirty_buffers()] == [("Book", "One")]
    assert slot_errors == []

    window.app.cmd_create("Next")
    window.ctx.changed.emit()
    window.module_list.setCurrentRow(0)
    assert window.app.current_module == "Next"
    window.workspace.set_focused_component("vocab")
    assert "(no vocab yet)" in window.workspace.focused.component.words.toPlainText()
    assert slot_errors == []


def test_study_finishes_cleanly_after_its_module_is_deleted(window, qt, slot_errors):
    window.app.cmd_add("word", target="Book", manual_def="a unit of language")
    window._open_study(["Book"])
    view = window.workspace.focused.component
    assert isinstance(view, StudyView)
    App(window.app.paths.root).cmd_delete("Book")
    view._reveal()
    view._grade(True)
    settle(window, qt)
    components = [pane.component for pane in window.workspace._panes]
    assert not any(isinstance(c, StudyView) for c in components)
    assert any(isinstance(c, VocabComponent) for c in components)
    assert window.app.current_module is None
    assert slot_errors == []
