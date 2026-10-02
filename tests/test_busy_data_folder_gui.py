"""A data folder busy with another process's save is reported, not a crash."""
import sys

import pytest
pytest.importorskip("PySide6")

from .test_gui_reliability import qt, window
from vocab.core.locking import DataFolderBusy
from vocab.gui.study_view import StudyView


@pytest.fixture
def slot_errors(monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(error))
    return errors


def test_study_answer_reports_a_busy_folder_and_keeps_the_card(window, monkeypatch, slot_errors):
    window.app.cmd_add("word", target="Book", manual_def="a unit of language")
    window._open_study(["Book"])
    view = window.workspace.focused.component
    assert isinstance(view, StudyView)
    def busy(correct):
        raise DataFolderBusy("another window is saving")
    monkeypatch.setattr(view.session, "answer", busy)
    view._reveal()
    view._grade(True)
    assert "Could not save review: another window is saving" in view.feedback.text()
    assert not view.session.done and slot_errors == []


def test_busy_folder_retries_recovery_copies_quietly(window, monkeypatch):
    buffer = window.ctx.notes.open("Book", "One")
    def busy(*args):
        raise DataFolderBusy("another window is saving")
    monkeypatch.setattr(window.app.storage, "save_note_draft", busy)
    buffer.document.setPlainText("typing")
    buffer.flush_recovery()
    assert buffer.recovery_pending and buffer.recovery_error == "" and buffer._timer.isActive()
