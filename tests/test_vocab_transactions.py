"""A vocabulary change updates words and progress together or not at all."""
import pytest

from vocab.cli.app import App
from vocab.core.storage import DataFileError


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_add("kept", target="Book", manual_def="original definition")
    return app


def words_bytes(app):
    return app.paths.words_file("Book").read_bytes()


def test_damaged_progress_stops_a_change_before_it_starts(app):
    app.paths.stats_file("Book").write_text("{damaged", encoding="utf-8")
    before = words_bytes(app)
    with pytest.raises(DataFileError):
        app.cmd_add("new", target="Book", manual_def="meaning")
    with pytest.raises(DataFileError):
        app.cmd_delete_word("kept", target="Book")
    assert words_bytes(app) == before


@pytest.mark.parametrize("change", ["add", "replace", "delete"])
def test_failed_progress_write_puts_the_words_back(app, monkeypatch, change):
    before = words_bytes(app)
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(app.storage, "save_stats", fail)
    with pytest.raises(OSError, match="disk full"):
        if change == "add":
            app.cmd_add("new", target="Book", manual_def="meaning")
        elif change == "replace":
            app.cmd_add("kept", target="Book", manual_def="replacement")
        else:
            app.cmd_delete_word("kept", target="Book")
    assert words_bytes(app) == before
    monkeypatch.undo()
    assert app.cmd_add("new", target="Book", manual_def="meaning").startswith("Added")  # retry works
