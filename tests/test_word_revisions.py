"""Mutation history shares vocabulary's atomic write and rollback."""
import json

import pytest

from vocab.cli.app import App
from vocab.core.models import Sense, Word
from vocab.core.storage import DataFileError, parse_import


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    return app


def test_deletion_history_survives_restart_and_unrelated_edits(app):
    storage = app.storage
    assert storage.raw_word_state("Book", "Gone") == (None, None)
    words = storage.load_words("Book")
    words.add(Word("gone", senses=[Sense("briefly present")]))
    storage.save_words(words)
    added, entry = storage.raw_word_state("Book", " GONE ")
    assert added and entry["senses"][0]["definition"] == "briefly present"
    words.remove("gone")
    storage.save_words(words)
    deleted, entry = storage.raw_word_state("Book", "gone")
    assert deleted and deleted != added and entry is None
    words.add(Word("other", senses=[Sense("another word")]))
    storage.save_words(words)
    before = storage.raw_word_state("Book", "other")
    storage.save_words(words)  # an unchanged save does not invalidate a lookup
    reopened = App(app.paths.root).storage
    assert reopened.raw_word_state("Book", "gone") == (deleted, None)
    assert reopened.raw_word_state("Book", "other") == before


def test_legacy_words_without_history_still_load(app):
    path = app.paths.words_file("Book")
    raw = {"schema": 1, "name": "Book", "words": {
        "legacy": {"dictionary": "manual", "senses": [{"definition": "old"}]}}}
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert app.storage.raw_word_state("Book", "legacy") == (None, raw["words"]["legacy"])
    assert app.storage.load_words("Book").has("legacy")


@pytest.mark.parametrize("raw", [{}, {"name": "Book"}])
def test_legacy_empty_words_file_can_still_be_saved(app, raw):
    app.paths.words_file("Book").write_text(json.dumps(raw), encoding="utf-8")
    words = app.storage.load_words("Book")
    words.add(Word("word", senses=[Sense("new")]))
    app.storage.save_words(words)
    assert app.storage.load_words("Book").has("word")


@pytest.mark.parametrize("history", [None, [], {"word": 7}, {"word": ""}, {" Word ": "token"}])
def test_malformed_history_is_reported_without_replacing_bytes(app, history):
    path = app.paths.words_file("Book")
    words = app.storage.load_words("Book")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["word_revisions"] = history
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    for call in (lambda: app.storage.load_words("Book"),
                 lambda: app.storage.raw_word_state("Book", "word"),
                 lambda: app.storage.save_words(words)):
        with pytest.raises(DataFileError, match="word_revisions") as error:
            call()
        assert error.value.path == path
    with pytest.raises(ValueError, match="word_revisions"):
        parse_import(raw)
    assert path.read_bytes() == before


def test_failed_stats_save_rolls_back_word_history_too(app, monkeypatch):
    words = app.storage.load_words("Book")
    words.add(Word("word", senses=[Sense("original")]))
    app.storage.save_words(words)
    before = app.paths.words_file("Book").read_bytes()
    state = app.storage.raw_word_state("Book", "word")
    words.remove("word")
    def fail(*args):
        raise OSError("cannot write progress")
    monkeypatch.setattr(app.storage, "save_stats", fail)
    with pytest.raises(OSError, match="cannot write progress"):
        app.storage.save_words_and_stats(words, {})
    assert app.paths.words_file("Book").read_bytes() == before
    assert app.storage.raw_word_state("Book", "word") == state
