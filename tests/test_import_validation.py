"""Imports are validated before anything is created, and published whole."""
import json
import math
from pathlib import Path

import pytest

from vocab.cli.app import App
from vocab.core.storage import Storage


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path / "home")
    app.cmd_create("Source")
    app.cmd_add("ephemeral", target="Source", manual_def="lasting a short time")
    app.cmd_add("Run", target="Source", manual_def="move fast")
    stats = app.storage.load_stats("Source")
    stats["ephemeral"].update(box=3, due=1_900_000_000.0, reps=4)
    app.storage.save_stats("Source", stats)
    return app


def write(tmp_path, payload, name="input.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload) if not isinstance(payload, str) else payload, encoding="utf-8")
    return path


def nothing_left(app):
    root = app.paths.root
    return (not list(root.glob(".vocab-staging-*")) and
            not list(app.paths.modules_dir.glob(".vocab-staging-*")))


def test_export_bundles_round_trip_with_and_without_progress(app, tmp_path):
    bundle = tmp_path / "bundle.json"
    app.cmd_export("Source", str(bundle), include_stats=True)
    app.cmd_import(str(bundle), "WithProgress", True)
    app.cmd_import(str(bundle), "Fresh", False)
    for name in ("WithProgress", "Fresh"):
        assert sorted(app.storage.load_words(name).words) == ["ephemeral", "run"]
    assert app.storage.load_stats("WithProgress")["ephemeral"]["box"] == 3
    assert app.storage.load_stats("Fresh") == {}


def test_words_json_with_or_without_schema_imports_its_words(app, tmp_path):
    words = json.loads(app.paths.words_file("Source").read_text(encoding="utf-8"))
    app.cmd_import(str(write(tmp_path, words, "with-schema.json")), "WithSchema", False)
    words.pop("schema")
    app.cmd_import(str(write(tmp_path, words, "no-schema.json")), "NoSchema", False)
    for name in ("WithSchema", "NoSchema"):
        assert sorted(app.storage.load_words(name).words) == ["ephemeral", "run"]


GOOD_ENTRY = {"senses": [{"definition": "a meaning"}]}


@pytest.mark.parametrize("payload, message", [
    ("[1, 2]", "not a Vocab Study export"),
    ({"name": "x"}, "not a Vocab Study export"),
    ({"schema": 99, "words": {"w": GOOD_ENTRY}}, "format version 99"),
    ({"words": {"w": {"senses": []}}}, "no definitions"),
    ({"words": {"w": {"senses": [{"definition": "  "}]}}}, "definition is empty"),
    ({"words": {"w": "a meaning"}}, "entry is not an object"),
    ({"words": {"Run": GOOD_ENTRY, "run": GOOD_ENTRY}}, "are the same word"),
])
def test_invalid_files_are_rejected_without_creating_anything(app, tmp_path, payload, message):
    path = write(tmp_path, payload)
    before = path.read_bytes()
    with pytest.raises(ValueError, match=message) as info:
        app.cmd_import(str(path), "Broken", False)
    assert "Nothing was imported" in str(info.value)
    assert not app.storage.exists("Broken") and nothing_left(app)
    assert path.read_bytes() == before


@pytest.mark.parametrize("card, message", [
    ({"box": 2, "due": "tomorrow"}, "'due' must be a time"),
    ({"box": 2, "due": math.nan}, "'due' must be a time"),
    ({"box": 2, "due": math.inf}, "'due' must be a time"),
    ({"box": 2.5}, "'box' must be a whole number"),
    (5, "progress is not an object"),
])
def test_unusable_progress_is_rejected_only_when_imported(app, tmp_path, card, message):
    bundle = {"words": {"schema": 1, "name": "X", "words": {"w": GOOD_ENTRY}}, "stats": {"w": card}}
    path = write(tmp_path, bundle)
    with pytest.raises(ValueError, match=message):
        app.cmd_import(str(path), "Broken", True)
    assert not app.storage.exists("Broken") and nothing_left(app)
    # Without --stats the progress is not used, so it does not block the words.
    app.cmd_import(str(path), "WordsOnly", False)
    assert app.storage.load_words("WordsOnly").has("w")
    assert app.storage.load_stats("WordsOnly") == {}


def test_progress_requested_from_a_file_without_any(app, tmp_path):
    path = write(tmp_path, {"words": {"w": GOOD_ENTRY}})
    with pytest.raises(ValueError, match="no progress to import"):
        app.cmd_import(str(path), "Broken", True)


@pytest.mark.parametrize("failing", ["save_words", "rename"])
def test_failed_publication_leaves_nothing_and_a_retry_works(app, tmp_path, monkeypatch, failing):
    bundle = tmp_path / "bundle.json"
    app.cmd_export("Source", str(bundle), include_stats=True)
    def fail(*args, **kwargs):
        raise OSError("injected failure")
    if failing == "save_words":
        monkeypatch.setattr(Storage, "save_words", fail)
    else:
        monkeypatch.setattr(Path, "rename", fail)
    with pytest.raises(OSError, match="injected failure"):
        app.cmd_import(str(bundle), "Copy", True)
    with pytest.raises(OSError, match="injected failure"):
        app.cmd_create("Fresh")
    monkeypatch.undo()
    assert not app.storage.exists("Copy") and not app.storage.exists("Fresh") and nothing_left(app)
    app.cmd_import(str(bundle), "Copy", True)
    app.cmd_create("Fresh")
    assert app.storage.load_stats("Copy")["ephemeral"]["reps"] == 4
