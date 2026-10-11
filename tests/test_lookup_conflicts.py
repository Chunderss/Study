"""A dictionary lookup must not overwrite the user's own or newer work."""
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
    assert "already in 'Book'" in message and "(Ephemeral :: ...)" in message
    kept = entry(app, "ephemeral")
    assert kept.dictionary == "manual" and kept.primary_definition() == "my careful definition"
    assert app.storage.load_stats("Book")["ephemeral"]["box"] == 4


def test_vocab_pane_lookup_hint_uses_the_panes_own_form(tmp_path):
    # The Vocab pane takes "word :: definition" as typed, without quotes, and
    # cannot take a word that contains '::', so its hint offers no form for one.
    app = App(tmp_path)
    app.cmd_create("Book")
    for word in ("ice cream", "std::vector"):
        app.cmd_add(word, target="Book", manual_def="mine")
    senses = [Sense("dictionary sense", pos="noun")]
    message = app.add_resolved_word("ice cream", "Book", senses, "wordnet")
    assert message.endswith("add it with your own definition (ice cream :: ...).")
    message = app.add_resolved_word("std::vector", "Book", senses, "wordnet")
    assert message.endswith("To replace it, add it with your own definition.")
    assert {word.primary_definition() for word in app.storage.load_words("Book").words.values()} \
        == {"mine"}


def test_explicit_manual_definition_still_replaces(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    looked_up(app)
    app.cmd_add("ephemeral", target="Book")
    assert app.cmd_add("ephemeral", target="Book", manual_def="mine").startswith("Updated")
    assert entry(app, "ephemeral").primary_definition() == "mine"


def test_lookup_keeps_a_word_added_while_it_ran(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    other = App(tmp_path)  # another pane or process
    looked_up(app, during=lambda: other.cmd_add("ephemeral", target="Book", manual_def="newer"))
    assert "already in 'Book'" in app.cmd_add("ephemeral", target="Book")
    assert entry(app, "ephemeral").primary_definition() == "newer"


def test_lookup_rejects_a_word_deleted_while_it_ran(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_add("ephemeral", target="Book", manual_def="first")
    expected = app.word_state("Book", "ephemeral")  # taken as the lookup starts
    App(tmp_path).cmd_delete_word("ephemeral", target="Book")
    with pytest.raises(ValueError, match="changed while it was being looked up"):
        app.add_resolved_word("ephemeral", "Book", [Sense("dictionary")], "wordnet", expected=expected)
    assert entry(app, "ephemeral") is None


def test_lookup_rejects_a_word_added_then_deleted_while_it_ran(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")

    def add_then_delete():
        other = App(tmp_path)
        other.cmd_add("Ephemeral", target="Book", manual_def="newer")
        other.cmd_delete_word("EPHEMERAL", target="Book")

    looked_up(app, during=add_then_delete)
    with pytest.raises(ValueError, match="changed while it was being looked up"):
        app.cmd_add("ephemeral", target="Book")
    assert entry(App(tmp_path), "ephemeral") is None


def test_independent_pending_lookups_can_both_commit(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    first = app.word_state("Book", "alpha")
    second = app.word_state("Book", "beta")
    app.add_resolved_word("alpha", "Book", [Sense("first")], "wordnet", expected=first)
    other = App(tmp_path)
    other.add_resolved_word("beta", "Book", [Sense("second")], "wordnet", expected=second)
    assert set(app.storage.load_words("Book").words) == {"alpha", "beta"}


def test_failed_word_write_restores_the_pending_lookup_state(tmp_path, monkeypatch):
    app = App(tmp_path)
    app.cmd_create("Book")
    expected = app.word_state("Book", "ephemeral")
    original = app.paths.words_file("Book").read_bytes()
    with monkeypatch.context() as patch:
        def fail_stats(*args):
            raise OSError("progress disk failure")
        patch.setattr(app.storage, "save_stats", fail_stats)
        with pytest.raises(OSError, match="progress disk failure"):
            app.cmd_add("ephemeral", target="Book", manual_def="not committed")
    assert app.paths.words_file("Book").read_bytes() == original
    assert app.word_state("Book", "ephemeral") == expected
    app.add_resolved_word("ephemeral", "Book", [Sense("dictionary")], "wordnet",
                          expected=expected)
    assert entry(app, "ephemeral").primary_definition() == "dictionary"


def test_lookup_into_a_module_with_a_damaged_manifest_still_works(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    manifest = app.paths.manifest_file("Book")
    manifest.write_text("{damaged", encoding="utf-8")
    looked_up(app)
    assert app.cmd_add("ephemeral", target="Book").startswith("Added")
    assert manifest.read_text(encoding="utf-8") == "{damaged"


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
