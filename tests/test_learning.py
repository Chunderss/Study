import json

import pytest

from vocab.cli.app import App
from vocab.algorithms.leitner import local_day_start
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
    assert learning.load("Book")[item["id"]]["card"]["due"] == local_day_start(200, 1)


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


def test_search_folds_case_and_latin_accents_as_grading_does():
    import unicodedata
    from vocab.core.learning import search_text
    from vocab.study.judge import _plain
    assert search_text("Café RÉSUMÉ Straße ŁÓDŹ ﬁne Æther") == "cafe resume strasse lodz fine aether"
    assert search_text("col·legi COŀLEGI") == "collegi collegi"   # the middle dot, as in grading
    assert search_text("हिन्दी йод") == "हिन्दी йод"               # other scripts keep their marks
    for word in ("ǿre", "Ǣsir", "ǽther", "Øre", "naïve", "żółty", "Ħal", "ĳssel", "Đuro",
                 "col·lecció", "Ŀlibre", "ðe", "þorn", "Dvořák", "Việt"):
        assert search_text(word) == _plain(unicodedata.normalize("NFKC", word).casefold()), word


def test_search_words_must_all_appear_in_question_passage_or_notes(learning):
    from vocab.core.learning import CaptureFilter
    item = capture(learning, prompt="Why does it work?", quote="Mitochondria supply energy.",
                   explanation="Ask about the résumé.")
    item = learning.review("Book", item["id"], item["revision"], "zebra answer", "good", now=100)
    matches = lambda query: learning.matches(item, CaptureFilter(query=query))
    assert matches("MITOCHONDRIA") and matches("resume why") and matches("chond") and matches("  ")
    assert not matches("mitochondria zebra")  # earlier review answers are not searched
    assert not matches("Mitochondria-supply")


def test_search_finds_typographic_punctuation_typed_on_a_keyboard(learning):
    from vocab.core.learning import CaptureFilter, search_text
    # As an EPUB or PDF selection gives it: curly apostrophes and quotes, a
    # non-breaking hyphen, an em dash, an en dash and soft hyphens.
    item = capture(learning, prompt="Who wrote \u201cSnow\u2011Bound\u201d?",
                   quote="I don\u2019t know the author\u2018s name \u2014 a well\u2011known "
                         "writer, 1866\u20131867.",
                   explanation="O\u2019Neill\u02bcs view: hy\u00adphen\u00adated.")
    assert search_text(item["quote"]) == "i don't know the author's name - a well-known writer, 1866-1867."
    matches = lambda query: learning.matches(item, CaptureFilter(query=query))
    for query in ("don't", "author's", "well-known", '"snow-bound"', "1866-1867", "o'neill's",
                  "hyphenated", "DON\u2019T", "Well\u2010Known"):
        assert matches(query), query
    assert matches("don") and not matches("dont") and not matches("don't-know")
    other = capture(learning, prompt="Plain", quote="I don't know a well-known \"name\".")
    for query in ("don\u2019t", "well\u2014known", "\u201cname\u201d"):  # and the other way round
        assert learning.matches(other, CaptureFilter(query=query)), query


def test_status_filters_split_questions_and_concepts(learning):
    from vocab.core.learning import CaptureFilter, STATUS_FILTERS
    open_q = capture(learning, kind="question")
    resolved = capture(learning, kind="question", resolved=True)
    due = capture(learning)
    later = capture(learning)
    later = learning.review("Book", later["id"], later["revision"], "answer", "good", now=1000)
    items = learning.load("Book")
    wanted = {"all": list(items), "open": [open_q["id"]], "resolved": [resolved["id"]],
              "questions": [open_q["id"], resolved["id"]], "due": [due["id"]],
              "later": [later["id"]], "concepts": [due["id"], later["id"]]}
    assert list(wanted) == list(STATUS_FILTERS)
    for status, ids in wanted.items():
        shown = [key for key, item in items.items()
                 if learning.matches(item, CaptureFilter(status=status), now=2000)]
        assert sorted(shown) == sorted(ids), status
    # The status shown in the list agrees with the due filter.
    assert learning.status(items[due["id"]], now=2000) == "Due now"
    assert learning.status(items[later["id"]], now=2000).startswith("Review ")


def test_source_filter_lists_documents_then_their_chapters_or_pages(learning):
    from vocab.core.learning import CaptureFilter, source_choice_label, source_choices
    sources = [{"kind": "epub", "filename": "b.epub", "chapter": 9},
               {"kind": "epub", "filename": "b.epub", "chapter": 1},
               {"kind": "pdf", "filename": "A.pdf", "page": 0},
               {"kind": "pdf", "filename": "A.pdf", "page": True},     # damaged place
               {"kind": "epub", "filename": "c.epub", "chapter": -1},  # damaged place
               {"kind": "pdf", "filename": "d.pdf", "page": "2"},      # damaged place
               {"filename": 7}, {}, {"label": "Old label"}]
    made = [capture(learning, source=source) for source in sources]
    items = learning.load("Book")
    keys = source_choices(items)
    assert [source_choice_label(key) for key in keys] == [
        "A.pdf", "A.pdf · page 1", "b.epub", "b.epub · chapter 2", "b.epub · chapter 10",
        "c.epub", "d.pdf", "Old label", "Manual captures"]
    # A chosen source whose captures are gone stays listed, in its place.
    kept = source_choices(items, keep=("file", "B2.pdf", "pdf", 2))
    assert [source_choice_label(key) for key in kept][5:7] == ["B2.pdf", "B2.pdf · page 3"]
    shown = lambda key: [index for index, item in enumerate(made)
                         if learning.matches(items[item["id"]], CaptureFilter(source=key))]
    assert shown(("file", "b.epub")) == [0, 1]
    assert shown(("file", "b.epub", "epub", 1)) == [1]
    assert shown(("file", "A.pdf")) == [2, 3]
    assert shown(("file", "A.pdf", "pdf", 0)) == [2]
    assert shown(("label", "Old label")) == [8]
    assert shown(("manual",)) == [6, 7]


def test_saved_filter_state_round_trips_and_drops_damaged_parts():
    from vocab.core.learning import CaptureFilter
    for chosen in (CaptureFilter("cell energy", ("file", "b.epub", "epub", 2), "due"),
                   CaptureFilter("", ("label", "Old label"), "open"),
                   CaptureFilter("x", ("manual",)), CaptureFilter()):
        assert CaptureFilter.from_state(json.loads(json.dumps(chosen.state()))) == chosen
    for damaged in ({"query": 5, "source": ["file", "b.epub", "epub", True], "status": "everything"},
                    {"query": None, "source": ["file", "b.epub", ["epub"], 2], "status": ["due"]},
                    {"source": ["file", ""]}, {"source": ["manual", "x"]}, {"source": "manual"},
                    ["not", "a", "dict"], None):
        assert CaptureFilter.from_state(damaged) == CaptureFilter(), damaged
    partly = CaptureFilter.from_state({"query": "cell", "source": ["pdf", 3], "status": "later"})
    assert partly == CaptureFilter("cell", None, "later")
    assert not CaptureFilter(query="  ·  ").active and CaptureFilter(status="due").active
