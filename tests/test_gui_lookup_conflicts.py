"""Asynchronous desktop lookups preserve changes made while they run."""
import threading

import pytest

from vocab.cli.app import App
from vocab.core.models import Sense
from .test_lookup_conflicts import entry

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
    assert any("already in 'Book'" in message for message in logged), logged


def test_reader_lookup_does_not_land_in_a_recreated_module(window, monkeypatch):
    release = blocked_lookup(window, monkeypatch)
    window.ctx.add_word("ephemeral", target="Book")
    window.app.cmd_delete("Book")
    window.app.cmd_create("Book")
    finish(window, release)
    assert entry(window.app, "ephemeral") is None


def test_late_reader_lookup_does_not_resurrect_an_added_then_deleted_word(window, monkeypatch):
    release = blocked_lookup(window, monkeypatch)
    logged, finished = [], []
    window.ctx.log.connect(logged.append)
    window.ctx.word_added.connect(lambda token, ok: finished.append(ok))
    try:
        window.ctx.add_word("ephemeral", target="Book")
        other = App(window.app.paths.root)
        other.cmd_add("Ephemeral", target="Book", manual_def="newer")
        other.cmd_delete_word("ephemeral", target="Book")
    finally:
        finish(window, release)
    assert entry(window.app, "ephemeral") is None
    assert finished == [False]
    assert any("changed while it was being looked up" in message for message in logged), logged


def manual_word_with_progress(window):
    window.app.cmd_add("ephemeral", target="Book", manual_def="my careful definition")
    stats = window.app.storage.load_stats("Book")
    stats["ephemeral"]["box"] = 4
    window.app.storage.save_stats("Book", stats)


def assert_kept(window):
    kept = entry(window.app, "ephemeral")
    assert kept.dictionary == "manual" and kept.primary_definition() == "my careful definition"
    assert window.app.storage.load_stats("Book")["ephemeral"]["box"] == 4


def test_reader_and_vocab_lookups_keep_an_existing_entry(window, monkeypatch):
    manual_word_with_progress(window)
    release = blocked_lookup(window, monkeypatch)
    release.set()
    logged, finished = [], []
    window.ctx.log.connect(logged.append)
    window.ctx.word_added.connect(lambda token, ok: finished.append(ok))
    window._add_highlighted_word("Ephemeral", "An ephemeral thing.", "Book")  # reader
    finish(window, release)
    vocab = window.workspace.focused.component
    vocab.add_input.setText("ephemeral")
    vocab._add()  # Vocab pane
    finish(window, release)
    assert_kept(window)
    assert finished == [True, True]
    assert sum("already in 'Book'" in message for message in logged) == 2, logged
    assert vocab.add_input.text() == ""


def test_console_add_keeps_and_manual_definition_replaces(window, monkeypatch):
    manual_word_with_progress(window)
    monkeypatch.setattr(window.app, "_lookup_senses",
                        lambda word, sentence="": ([Sense("dictionary sense")], "wordnet", ""))
    window.workspace.set_focused_component("console")
    console = window.workspace.focused.component
    console.cmd.setText("ADD Ephemeral")
    console._run()
    assert_kept(window)
    console.cmd.setText("ADD ephemeral :: replaced on purpose")
    console._run()
    assert entry(window.app, "ephemeral").primary_definition() == "replaced on purpose"
    assert window.app.storage.load_stats("Book")["ephemeral"]["box"] == 4


def test_reader_lookup_keeps_its_sentence_and_names_the_guess(window, monkeypatch):
    senses = [Sense("communicate or signal with a flag", "verb"), Sense("become less intense", "verb")]
    monkeypatch.setattr(window.app, "_lookup_senses",
                        lambda word, sentence="": (list(senses), "wordnet", ""))
    window._add_highlighted_word("flag", "His energy began to flag.", "Book")
    finish(window, threading.Event())
    assert entry(window.app, "flag").context == "His energy began to flag."
    assert 'Guessed from "His energy began to flag."; 1 more sense saved.' in window.message.text()
