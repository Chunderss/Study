import json

import pytest

from vocab.cli.app import App
from vocab.core.learning import LearningStore


@pytest.fixture
def learning(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    return LearningStore(app.storage)


def capture(store, **overrides):
    data = dict(kind="concept", prompt="Why does spacing help?", quote="Practice is distributed across time.",
                source={"kind": "pdf", "filename": "book.pdf", "page": 2})
    data.update(overrides)
    return store.save_capture("Book", **data)


def test_capture_review_round_trip_does_not_touch_vocab_progress(learning):
    item = capture(learning)
    assert learning.due(learning.load("Book"), 100) == [item["id"]]
    reviewed = learning.review("Book", item["id"], item["revision"], "I retrieve it after a delay.", "good", now=100)
    reloaded = LearningStore(learning.storage).load("Book")[item["id"]]
    assert reloaded == reviewed
    assert reloaded["source"]["page"] == 2
    assert reloaded["attempts"][0]["answer"] == "I retrieve it after a delay."
    assert learning.due({item["id"]: reloaded}, 101) == []
    assert learning.storage.load_stats("Book") == {}
    learning.review("Book", item["id"], reloaded["revision"], "I forgot the mechanism.", "again", now=200)
    assert learning.load("Book")[item["id"]]["card"]["due"] == 200 + 86400


def test_open_questions_persist_without_scheduled_reviews(learning):
    item = capture(learning, kind="question")
    assert not learning.due(learning.load("Book"))
    assert not item["resolved"]
    with pytest.raises(ValueError, match="Only concepts"):
        learning.review("Book", item["id"], item["revision"], "answer", "good")
    saved = capture(learning, kind="question", item_id=item["id"], revision=item["revision"], resolved=True)
    assert learning.load("Book")[item["id"]]["resolved"]
    assert saved["attempts"] == []


def test_stale_editor_cannot_erase_review_or_another_capture(learning):
    item = capture(learning)
    other = capture(learning, prompt="A different question?")
    learning.review("Book", item["id"], item["revision"], "my answer", "good")
    with pytest.raises(ValueError, match="another window"):
        capture(learning, item_id=item["id"], revision=item["revision"], explanation="stale edit")
    assert len(learning.load("Book")) == 2
    assert learning.load("Book")[other["id"]]["prompt"] == "A different question?"
    assert len(learning.load("Book")[item["id"]]["attempts"]) == 1


def test_missing_module_and_deleted_entry_are_not_recreated(learning):
    item = capture(learning)
    learning.delete("Book", item["id"], item["revision"])
    with pytest.raises(ValueError, match="deleted"):
        capture(learning, item_id=item["id"], revision=item["revision"])
    learning.storage.delete("Book")
    with pytest.raises(Exception, match="does not exist"):
        capture(learning)
    assert not learning.storage.paths.module_dir("Book").exists()


@pytest.mark.parametrize("raw", ['{"schema":99,"items":{}}', '{broken', '{"schema":1,"items":{"a":{}}}'])
def test_unreadable_learning_file_is_not_overwritten(learning, raw):
    path = learning.storage.paths.module_dir("Book") / "learning.json"
    path.write_text(raw)
    with pytest.raises(ValueError):
        capture(learning)
    assert path.read_text() == raw


def test_invalid_capture_or_review_never_changes_disk(learning):
    item = capture(learning)
    path = learning.storage.paths.module_dir("Book") / "learning.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        capture(learning, quote=" ")
    with pytest.raises(ValueError):
        learning.review("Book", item["id"], item["revision"], "", "good")
    assert path.read_bytes() == before
