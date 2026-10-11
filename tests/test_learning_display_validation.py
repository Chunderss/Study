"""Tolerated legacy source details remain displayable; unusable times are named."""
import json

import pytest

from vocab.cli.app import App
from vocab.core.learning import LearningStore, source_choices, source_keys, source_label
from vocab.core.storage import DataFileError


@pytest.mark.parametrize("source, label, key", [
    ({"kind": "pdf", "filename": "book.pdf", "page": "one"}, "book.pdf", ("file", "book.pdf")),
    ({"kind": "pdf", "filename": "book.pdf", "page": True}, "book.pdf", ("file", "book.pdf")),
    ({"kind": "epub", "filename": "book.epub", "chapter": -1}, "book.epub", ("file", "book.epub")),
    ({"kind": [], "filename": "book.pdf"}, "book.pdf", ("file", "book.pdf")),
    ({"filename": 7, "label": ["bad"]}, "Manual capture", ("manual",)),
    ({"filename": None, "label": "Old source"}, "Old source", ("label", "Old source")),
])
def test_legacy_source_details_do_not_break_learning_display(tmp_path, source, label, key):
    app = App(tmp_path)
    app.cmd_create("Book")
    store = LearningStore(app.storage)
    item = store.save_capture("Book", kind="concept", prompt="Why?", quote="Passage", source=source)
    loaded = store.load("Book")[item["id"]]
    # These are the helpers called by list filtering, selection and capture dialogs.
    assert source_label(loaded["source"]) == label
    assert source_keys(loaded["source"]) == (key, None)
    assert source_choices({item["id"]: loaded}) == [key]
    assert store.status(loaded) == "Due now"


@pytest.mark.parametrize("field", ["created", "attempt", "due", "last"])
def test_oversized_json_integer_has_a_contextual_error_and_preserves_bytes(tmp_path, field):
    app = App(tmp_path)
    app.cmd_create("Book")
    store = LearningStore(app.storage)
    item = store.save_capture("Book", kind="concept", prompt="Why?", quote="Passage")
    path = store._path("Book")
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data["items"][item["id"]]
    huge = 10 ** 1000
    if field == "created":
        raw[field] = huge
    elif field == "attempt":
        raw["attempts"] = [{"time": huge, "answer": "Because", "rating": "good"}]
    else:
        raw["card"][field] = huge
    path.write_text(json.dumps(data), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(DataFileError) as error:
        store.load("Book")
    assert error.value.path == path and item["id"] in str(error.value)
    assert path.read_bytes() == before


def test_finite_out_of_range_due_date_keeps_existing_safe_display(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    store = LearningStore(app.storage)
    item = store.save_capture("Book", kind="concept", prompt="Why?", quote="Passage")
    path = store._path("Book")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][item["id"]]["card"]["due"] = 1e30
    path.write_text(json.dumps(data), encoding="utf-8")
    assert store.status(store.load("Book")[item["id"]]) == "Review later"
