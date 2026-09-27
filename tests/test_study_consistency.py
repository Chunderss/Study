"""Reviews and counts must reflect the latest persisted vocabulary state."""
import json

import pytest

from vocab.cli.app import App
from vocab.core.paths import Paths
from vocab.study.session import StudyCardUnavailable, StudySession


class CaseInsensitivePaths(Paths):
    def module_dir(self, name):
        path = super().module_dir(name)
        for existing in self.modules_dir.iterdir():
            if existing.name.casefold() == path.name.casefold():
                return existing
        return path


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.cmd_add("alpha", manual_def="first letter")
    app.cmd_add("beta", manual_def="second letter")
    return app


def test_overlapping_sessions_keep_latest_review_and_skip_stale_card(app):
    first = StudySession(app.storage, app.scheduler, ["Book"])
    second = StudySession(app.storage, app.scheduler, ["Book"])
    first.answer(False)
    saved = app.storage.load_stats("Book")["alpha"]
    with pytest.raises(StudyCardUnavailable, match="progress changed"):
        second.answer(True)
    assert app.storage.load_stats("Book")["alpha"] == saved
    assert saved["lapses"] == 1
    assert second.reviewed == 0
    assert second.current().word == "beta"
    second.answer(True)
    assert second.reviewed == 1
    assert "Skipped 1" in second.finish()


def test_legacy_word_without_timestamp_is_not_mistaken_for_an_edit(app, monkeypatch):
    path = app.paths.words_file("Book")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["words"]["alpha"].pop("added")
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("vocab.core.models.utcnow_iso", lambda: "2026-01-01T00:00:00Z")
    session = StudySession(app.storage, app.scheduler, ["Book"])
    monkeypatch.setattr("vocab.core.models.utcnow_iso", lambda: "2026-01-01T00:00:01Z")
    session.answer(True)
    assert session.reviewed == 1
    assert app.storage.load_stats("Book")["alpha"]["reps"] == 1


@pytest.mark.parametrize("change", ["delete", "edit"])
def test_changed_word_is_skipped_without_grading_next_card(app, change):
    session = StudySession(app.storage, app.scheduler, ["Book"])
    if change == "delete":
        app.cmd_delete_word("alpha")
    else:
        app.cmd_add("alpha", manual_def="a different definition")
    saved = app.storage.load_stats("Book")
    with pytest.raises(StudyCardUnavailable):
        session.answer(True)
    assert app.storage.load_stats("Book") == saved
    assert session.reviewed == 0
    assert session.current().word == "beta"


def test_deleted_module_is_skipped_without_recreation(app):
    session = StudySession(app.storage, app.scheduler, ["Book"])
    app.cmd_delete("Book")
    for _ in range(2):
        with pytest.raises(StudyCardUnavailable, match="module was deleted"):
            session.answer(False)
    assert session.done
    assert not app.paths.module_dir("Book").exists()


def test_failed_review_save_preserves_card_for_retry(app, monkeypatch):
    session = StudySession(app.storage, app.scheduler, ["Book"])
    original_save = app.storage.save_stats

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(app.storage, "save_stats", fail)
    with pytest.raises(OSError, match="disk full"):
        session.answer(True)
    assert session.current().word == "alpha"
    assert session.reviewed == 0
    assert session.result.skipped == 0
    monkeypatch.setattr(app.storage, "save_stats", original_save)
    session.answer(True)
    assert app.storage.load_stats("Book")["alpha"]["reps"] == 1


@pytest.mark.parametrize("mechanical", [False, True])
def test_terminal_study_continues_after_current_word_is_deleted(app, mechanical):
    app.config.mechanical_repetition = mechanical
    deleted = False
    output = []

    def prompt(_text):
        nonlocal deleted
        if not deleted:
            app.cmd_delete_word("alpha")
            deleted = True
        return "second letter" if mechanical else "y"

    summary = app.run_study(["Book"], prompt, output.append)
    assert "1/1 correct" in summary
    assert "Skipped 1" in summary
    assert any("word was deleted" in line for line in output)
    assert app.storage.load_stats("Book")["beta"]["reps"] == 1


def test_imported_due_count_matches_study_without_writing_progress(app, tmp_path):
    export = tmp_path / "book.json"
    app.cmd_export("Book", str(export))
    app.cmd_import(str(export), "Imported", False)
    assert app.due_count("Imported") == 2
    assert "2 words, 2 due now" in app.cmd_stats("Imported")
    assert app.storage.load_stats("Imported") == {}
    assert StudySession(app.storage, app.scheduler, ["Imported"]).total == 2


def test_due_count_ignores_progress_for_words_no_longer_present(app):
    stats = app.storage.load_stats("Book")
    stats["orphan"] = app.scheduler.new_card()
    app.storage.save_stats("Book", stats)
    assert app.due_count("Book") == 2
    assert "2 due now" in app.cmd_stats("Book")


def test_case_insensitive_delete_clears_selected_module(app, monkeypatch):
    monkeypatch.setattr(app.storage, "paths", CaseInsensitivePaths(app.paths.root))
    assert "'Book'" in app.cmd_delete("book")
    assert app.current_module is None
    assert not app.storage.exists("Book")


def test_deleting_distinct_lowercase_module_keeps_selection(app):
    if app.storage.exists("book"):
        pytest.skip("Requires a case-sensitive filesystem")
    app.cmd_create("book")
    app.cmd_delete("book")
    assert app.current_module == "Book"
    assert app.storage.exists("Book")
