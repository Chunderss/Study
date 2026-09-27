import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QMessageBox

from .test_gui_reliability import qt, window
from .test_study_consistency import CaseInsensitivePaths
from vocab.gui.study_view import StudyView
from vocab.study.judge import KeywordJudge
from vocab.study.session import StudySession


@pytest.mark.parametrize("mechanical", [False, True])
def test_deleted_card_advances_gui_without_recording_an_answer(window, qt, mechanical):
    for word in ("alpha", "beta"):
        window.app.cmd_add(word, manual_def="first letter")
    session = StudySession(window.app.storage, window.app.scheduler, ["Book"],
                           mechanical=mechanical, judge=KeywordJudge())
    view = StudyView(session)
    window.app.cmd_delete_word("alpha")
    if mechanical:
        view.input.setText("first letter")
        view._submit_typed()
    else:
        view._reveal()
        view._grade(True)
    assert session.current().word == "beta"
    assert view.word.text() == "beta"
    assert "word was deleted" in view.feedback.text()
    assert session.reviewed == 0
    assert not view._awaiting_next
    view.deleteLater()
    qt.processEvents()


def test_stale_last_card_finishes_gui_with_skip_summary(window, qt):
    window.app.cmd_add("alpha", manual_def="first letter")
    first = StudySession(window.app.storage, window.app.scheduler, ["Book"])
    stale = StudySession(window.app.storage, window.app.scheduler, ["Book"])
    view = StudyView(stale)
    summaries = []
    view.finished.connect(summaries.append)
    first.answer(False)
    view._reveal()
    view._grade(True)
    assert view._ended
    assert len(summaries) == 1 and "Skipped 1" in summaries[0]
    assert window.app.storage.load_stats("Book")["alpha"]["lapses"] == 1
    view.deleteLater()
    qt.processEvents()


def test_failed_mechanical_save_keeps_typed_answer(window, qt, monkeypatch):
    window.app.cmd_add("alpha", manual_def="first letter")
    session = StudySession(window.app.storage, window.app.scheduler, ["Book"],
                           mechanical=True, judge=KeywordJudge())
    view = StudyView(session)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(window.app.storage, "save_stats", fail)
    view.input.setText("first letter")
    view._submit_typed()
    assert view.input.text() == "first letter"
    assert view.input.isEnabled()
    assert "disk full" in view.feedback.text()
    assert session.reviewed == 0
    view.deleteLater()
    qt.processEvents()


def test_imported_words_appear_due_in_status_bar(window, tmp_path):
    window.app.cmd_add("alpha", manual_def="first letter")
    export = tmp_path / "book.json"
    window.app.cmd_export("Book", str(export))
    window.app.cmd_import(str(export), "Imported", False)
    window.app.cmd_use("Imported")
    window._refresh_status()
    assert "due: 1" in window.status.text()
    assert window.app.storage.load_stats("Imported") == {}


@pytest.mark.parametrize("confirmed", [True, False])
def test_console_case_insensitive_delete_handles_canonical_drafts(window, monkeypatch, confirmed):
    monkeypatch.setattr(window.app.storage, "paths", CaseInsensitivePaths(window.app.paths.root))
    buffer = window.ctx.notes.open("Book", "One")
    buffer.document.setPlainText("unsaved changes")
    buffer.flush_recovery()
    window.workspace.set_focused_component("console")
    console = window.workspace.focused.component
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Yes if confirmed else QMessageBox.No)
    console.cmd.setText("DELETE book")
    console._run()
    if not confirmed:
        assert window.app.current_module == "Book"
        assert window.ctx.notes.buffers[("Book", "One")] is buffer
        assert buffer.dirty
        assert window.app.storage.load_note_draft("Book", "One")["content"] == "unsaved changes"
        return
    assert window.app.current_module is None
    assert window.module_list.count() == 0
    assert not window.ctx.notes.buffers
    assert not buffer._timer.isActive()
    assert not window.app.storage.exists("Book")
