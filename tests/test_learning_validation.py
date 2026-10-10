"""learning.json is checked against everything the app reads from it."""
import json
import sys

import pytest

from vocab.cli.app import App
from vocab.core.learning import LearningStore
from vocab.core.storage import DataFileError


@pytest.fixture
def store(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    store = LearningStore(app.storage)
    concept = store.save_capture("Book", kind="concept", prompt="Why?", quote="A passage.")
    store.review("Book", concept["id"], concept["revision"], "Because.", "good", now=1000)
    store.save_capture("Book", kind="question", prompt="What?", quote="Another passage.")
    return store, concept["id"]


def corrupt(store, item_id, change):
    path = store._path("Book")
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data, data["items"][item_id])
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_valid_captures_load(store):
    store, item_id = store
    items = store.load("Book")
    assert len(items) == 2 and items[item_id]["attempts"][0]["answer"] == "Because."


@pytest.mark.parametrize("change, message", [
    (lambda data, item: item.pop("resolved"), "'resolved' must be true or false"),
    (lambda data, item: item.update(resolved="yes"), "'resolved' must be true or false"),
    (lambda data, item: item.update(created="now"), "'created' must be a time"),
    (lambda data, item: item.update(revision=-1), "'revision' must be a whole number"),
    (lambda data, item: item.update(attempts={}), "'attempts' must be a list"),
    (lambda data, item: item["attempts"][0].pop("answer"), "an attempt is incomplete"),
    (lambda data, item: item["card"].update(due="tomorrow"), "'due' must be a time"),
    (lambda data, item: item["card"].pop("due"), "progress has no due time"),
    (lambda data, item: item.update(kind="note"), "'kind' must be concept or question"),
    (lambda data, item: item.update(source=[]), "'source' must be an object"),
    (lambda data, item: data.update(schema=2), "unsupported version 2"),
])
def test_damaged_captures_are_named_and_left_unchanged(store, change, message):
    store, item_id = store
    path = corrupt(store, item_id, change)
    before = path.read_bytes()
    with pytest.raises(DataFileError, match=message) as info:
        store.load("Book")
    assert str(path) in str(info.value)
    assert path.read_bytes() == before


pytest.importorskip("PySide6")
from .test_gui_reliability import qt, window  # noqa: E402,F401


def test_learning_pane_reports_a_damaged_capture_instead_of_failing(window, monkeypatch):
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(error))
    store = LearningStore(window.app.storage)
    question = store.save_capture("Book", kind="question", prompt="What?", quote="A passage.")
    corrupt(store, question["id"], lambda data, item: item.pop("resolved"))
    window.workspace.set_focused_component("learning")
    pane = window.workspace.focused.component
    assert "'resolved' must be true or false" in pane.summary.text()
    assert errors == []
