"""A dictionary lookup must not overwrite the user's own or newer work."""
import threading

import pytest

from vocab.cli.app import App
from vocab.core.models import Sense


def looked_up(app, during=None):
    def lookup(word, sentence=""):
        if during:
            during()
        return [Sense("dictionary sense", pos="noun")], "wordnet", ""
    app._lookup_senses = lookup


def entry(app, word, module="Book"):
    words = app.storage.load_words(module)
    return words.words.get(words.normalize_key(word))


def test_lookup_keeps_a_manual_definition_and_its_progress(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_add("ephemeral", target="Book", manual_def="my careful definition")
    stats = app.storage.load_stats("Book")
    stats["ephemeral"]["box"] = 4
    app.storage.save_stats("Book", stats)
    looked_up(app)
    message = app.cmd_add("Ephemeral", target="Book")  # a case variant, too
    assert "already in 'Book'" in message and "ADD Ephemeral ::" in message
    kept = entry(app, "ephemeral")
    assert kept.dictionary == "manual" and kept.primary_definition() == "my careful definition"
    assert app.storage.load_stats("Book")["ephemeral"]["box"] == 4


def test_explicit_manual_definition_still_replaces(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    looked_up(app)
    app.cmd_add("ephemeral", target="Book")
    assert app.cmd_add("ephemeral", target="Book", manual_def="mine").startswith("Updated")
    assert entry(app, "ephemeral").primary_definition() == "mine"


def test_lookup_rejects_a_word_edited_while_it_ran(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    other = App(tmp_path)  # another pane or process
    looked_up(app, during=lambda: other.cmd_add("ephemeral", target="Book", manual_def="newer"))
    with pytest.raises(ValueError, match="changed while it was being looked up"):
        app.cmd_add("ephemeral", target="Book")
    assert entry(app, "ephemeral").primary_definition() == "newer"


def test_lookup_rejects_a_module_recreated_under_the_same_name(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    other = App(tmp_path)
    def recreate():
        other.cmd_delete("Book")
        other.cmd_create("Book")
    looked_up(app, during=recreate)
    with pytest.raises(ValueError, match="changed while it was being looked up"):
        app.cmd_add("ephemeral", target="Book")
    assert entry(app, "ephemeral") is None


def test_slow_lookups_work_for_legacy_modules_and_words(tmp_path):
    # Legacy data lacks the timestamps that parsing would fill in with "now".
    import json, time
    app = App(tmp_path)
    app.cmd_create("Book")
    app.paths.manifest_file("Book").write_text(json.dumps({"schema": 1, "name": "Book"}))
    app.paths.words_file("Book").write_text(json.dumps(
        {"name": "Book", "words": {"Old": {"senses": [{"definition": "legacy"}]}}}))
    looked_up(app, during=lambda: time.sleep(1.1))
    assert app.cmd_add("new", target="Book").startswith("Added")
    assert "already in 'Book'" in app.cmd_add("old", target="Book")


pytest.importorskip("PySide6")
from PySide6.QtTest import QTest  # noqa: E402

from .test_gui_reliability import qt, window  # noqa: E402,F401


def blocked_lookup(window, monkeypatch):
    release = threading.Event()
    def lookup(word, sentence=""):
        assert release.wait(5)
        return [Sense("dictionary sense")], "wordnet", ""
    monkeypatch.setattr(window.app, "_lookup_senses", lookup)
    return release


def finish(window, release):
    release.set()
    window.ctx._pool.waitForDone(5000)
    for _ in range(200):
        if not window.ctx.busy:
            break
        QTest.qWait(10)


def test_reader_lookup_does_not_overwrite_a_definition_saved_meanwhile(window, monkeypatch):
    release = blocked_lookup(window, monkeypatch)
    logged = []
    window.ctx.log.connect(logged.append)
    window.ctx.add_word("ephemeral", target="Book")
    window.ctx.add_word("ephemeral", manual="saved from another pane", target="Book")
    finish(window, release)
    assert entry(window.app, "ephemeral").primary_definition() == "saved from another pane"
    assert any("changed while it was being looked up" in message for message in logged), logged


def test_reader_lookup_does_not_land_in_a_recreated_module(window, monkeypatch):
    release = blocked_lookup(window, monkeypatch)
    window.ctx.add_word("ephemeral", target="Book")
    window.app.cmd_delete("Book")
    window.app.cmd_create("Book")
    finish(window, release)
    assert entry(window.app, "ephemeral") is None
