"""Which sense a word studies is visible and can be corrected.

A lookup keeps the sentence it ranked the senses for and says how the first
sense was chosen. Real WordNet comes from NLTK_DATA; mocked dictionaries cover
the rest.
"""
import io
import json

import pytest

from tests import real_nltk
from vocab.cli.app import App
from vocab.cli.parser import parse
from vocab.cli.repl import Repl
from vocab.core.models import Sense
from vocab.core.senses import CONTEXT_LIMIT
from vocab.study.disambiguate import NlpDisambiguator

VERB_USE = "His energy began to flag."
THREE = [Sense("a rectangular emblem of cloth", "noun"),
         Sense("become less intense", "verb", "his energy flagged"),
         Sense("decorate with flags", "verb")]


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Repl, "_startup_checks", lambda self: None)
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    app.cmd_use("Book")
    return app


def say(app, line):
    """The reply to a typed command, or the error the terminal shows as '! ...'."""
    try:
        return Repl(app, in_stream=io.StringIO(), out_stream=io.StringIO()).dispatch(parse(line))
    except Exception as error:
        return f"! {error}"


def raw(app, word, module="Book"):
    return json.loads(app.paths.words_file(module).read_text("utf-8"))["words"].get(word)


def looked_up(app, monkeypatch, senses=THREE, dictionary="wordnet"):
    monkeypatch.setattr(app, "_lookup_senses",
                        lambda word, sentence="": (list(senses), dictionary, ""))


class Dictionary:
    """A dictionary that returns THREE, for the real ranking path."""
    id = "wordnet"
    requires_network = False

    def __init__(self, senses=THREE):
        self.senses = senses

    def available(self):
        return True

    def lookup(self, word):
        return list(self.senses)


def tag_as(monkeypatch, wn_pos, tagger=True):
    monkeypatch.setattr(NlpDisambiguator, "_ensure_tagger", lambda self: tagger)
    monkeypatch.setattr(NlpDisambiguator, "_wn_pos_of", lambda self, word, sentence: wn_pos)


# ---- the sentence is kept and the guess is named ---------------------------
def test_lookup_saves_its_sentence_and_names_the_guess(app, monkeypatch):
    looked_up(app, monkeypatch)
    reply = app.cmd_add("flag", sentence=VERB_USE)
    assert raw(app, "flag")["context"] == VERB_USE
    assert f'Guessed from "{VERB_USE}"; 2 more senses saved.' in reply
    reply = app.cmd_add("bank")  # typed, no sentence: stored exactly as before
    assert set(raw(app, "bank")) == {"dictionary", "added", "senses"}
    assert "The dictionary's first sense; 2 more senses saved." in reply
    reply = app.cmd_add("mine", manual_def="my own")
    assert set(raw(app, "mine")) == {"dictionary", "added", "senses"}
    assert "sense" not in reply.splitlines()[-1]  # nothing was guessed


def test_the_reply_says_how_the_first_sense_was_chosen(app, monkeypatch):
    monkeypatch.setattr(app, "effective_dictionary", lambda: (Dictionary(), ""))
    tag_as(monkeypatch, "v")
    reply = app.cmd_add("flag", sentence=VERB_USE)
    assert reply.splitlines()[1] == "    verb: become less intense"
    assert reply.splitlines()[2].startswith(f'    Guessed from "{VERB_USE}"; 2 more senses saved.')
    tag_as(monkeypatch, None, tagger=False)
    reply = app.cmd_add("flagged", sentence=VERB_USE)
    assert "without part-of-speech tagging (DOCTOR explains)" in reply
    app.config.disambiguator = "base"  # keeps the dictionary's order
    reply = app.cmd_add("flags", sentence=VERB_USE)
    assert reply.splitlines()[1] == "    noun: a rectangular emblem of cloth"
    assert ("The dictionary's first sense (DISAMBIG base does not use the sentence); "
            "2 more senses saved.") in reply
    app.config.disambiguator = "nlp"

    def broken(self, word, sentence, senses):
        raise RuntimeError("tagger crashed")
    monkeypatch.setattr(NlpDisambiguator, "rank", broken)
    reply = app.cmd_add("flagging", sentence=VERB_USE)
    assert reply.splitlines()[1] == "    noun: a rectangular emblem of cloth"
    assert "The dictionary's first sense (choosing one for the sentence failed: tagger crashed)" in reply
    monkeypatch.setattr(app, "effective_dictionary", lambda: (Dictionary(THREE[:1]), ""))
    assert len(app.cmd_add("flagger", sentence=VERB_USE).splitlines()) == 2  # one sense: no line


def test_a_long_selection_keeps_only_the_text_around_the_word(app, monkeypatch):
    looked_up(app, monkeypatch)
    passage = " ".join(["Before the word there is a long stretch of text."] * 15
                       + [VERB_USE] + ["After it the paragraph goes on and on."] * 15)
    app.cmd_add("flag", sentence=passage)
    context = raw(app, "flag")["context"]
    assert len(context) <= CONTEXT_LIMIT and VERB_USE in context
    assert context[0] == "…" and context[-1] == "…"
    inner = context[1:-1]
    start = passage.index(inner)
    assert passage[start - 1] == " " and passage[start + len(inner)] == " "  # cut at spaces


@pytest.mark.parametrize("sentence", ["flag", "Flag.", "  “flag”  "])
def test_a_sentence_that_is_only_the_word_is_not_kept(app, monkeypatch, sentence):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=sentence)  # the reader's fallback sends the selection
    assert "context" not in raw(app, "flag")
    app.cmd_add("flags", sentence=f"Two {sentence.strip()} poles.")
    assert raw(app, "flags")["context"] == f"Two {sentence.strip()} poles."


def test_your_own_definition_keeps_the_sentence_you_collected(app, monkeypatch):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=VERB_USE)
    say(app, "ADD flag :: to tire")
    entry = raw(app, "flag")
    assert entry["dictionary"] == "manual" and entry["context"] == VERB_USE


@pytest.mark.parametrize("extra, problem", [
    ({"context": 5}, "'context' must be text"),
    ({"context": None}, "'context' must be text"),
    ({"context": "x" * (CONTEXT_LIMIT + 1)}, f"longer than {CONTEXT_LIMIT}")])
def test_import_rejects_an_unusable_sentence(app, tmp_path, extra, problem):
    entry = {"senses": [{"definition": d} for d in ("a", "b", "c")], **extra}
    source = tmp_path / "in.json"
    source.write_text(json.dumps({"name": "In", "words": {"w": entry}}), encoding="utf-8")
    with pytest.raises(ValueError, match=problem):
        app.cmd_import(str(source), None, with_stats=False)
    assert not app.storage.exists("In")


def test_export_and_import_keep_the_sentence(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=VERB_USE)
    say(app, f"EXPORT Book {tmp_path / 'out.json'}")
    say(app, f"IMPORT {tmp_path / 'out.json'} as Copy")
    assert raw(app, "flag", "Copy") == raw(app, "flag")
    assert raw(app, "flag", "Copy")["context"] == VERB_USE


def test_a_null_sentence_in_a_hand_edited_file_is_not_shown_as_none(app):
    path = app.paths.words_file("Book")
    path.write_text(json.dumps({"name": "Book", "words": {"flag": {
        "senses": [{"definition": "a"}], "context": None}}}), encoding="utf-8")
    before = path.read_bytes()
    word = app.storage.load_words("Book").words["flag"]
    assert word.context == "" and "context" not in word.to_dict()
    assert path.read_bytes() == before  # loading rewrites nothing


def test_real_wordnet_lookup_from_a_sentence_names_its_guess(app, monkeypatch):
    real_nltk.require("corpora/wordnet.zip")
    app.config.active_dictionary = "wordnet"
    tag_as(monkeypatch, "v")
    reply = app.cmd_add("flag", sentence=VERB_USE)
    # The guess is wrong here ("become less intense" fits), so say it is one.
    assert reply.splitlines()[1] == "    verb: communicate or signal with a flag"
    assert reply.splitlines()[2].startswith(f'    Guessed from "{VERB_USE}"; 5 more senses saved.')
    assert raw(app, "flag")["context"] == VERB_USE


# ---- SENSES lists them -----------------------------------------------------
def numbered(out):
    """{number: row} for the numbered rows of a SENSES list."""
    rows = {}
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit():
            rows[int(parts[0])] = line
    return rows


def test_senses_lists_the_saved_senses_and_the_sentence(app, monkeypatch):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=VERB_USE)
    out = say(app, "SENSES flag")
    assert out.splitlines()[:2] == [
        "'flag' in 'Book' [wordnet]",
        f'First sense chosen automatically.  Sentence: "{VERB_USE}"']
    rows = numbered(out)
    assert rows == {1: "   1 * noun: a rectangular emblem of cloth",
                    2: "   2   verb: become less intense",
                    3: "   3   verb: decorate with flags"}
    assert '         "his energy flagged"' in out.splitlines()  # the example, under its sense
    assert "SENSES flag --all  also lists the dictionary's other senses." in out
    assert say(app, "SENSES").startswith("Usage: SENSES <word>")
    assert say(app, "SENSES bank") == "! 'bank' is not in 'Book'."


def test_senses_of_a_manual_definition_says_so(app, monkeypatch):
    looked_up(app, monkeypatch)
    app.cmd_add("ephemeral", manual_def="my careful definition")
    out = say(app, "SENSES ephemeral --all")
    assert out.splitlines()[0] == "'ephemeral' in 'Book': your own definition"
    assert numbered(out) == {1: "   1 * my careful definition"}
    assert "DEL ephemeral  then  ADD ephemeral   (its progress starts over)" in out


def test_senses_all_adds_the_dictionarys_other_real_wordnet_senses(app):
    real_nltk.require("corpora/wordnet.zip")
    app.config.active_dictionary = "wordnet"
    app.cmd_add("flag")  # typed, no sentence: WordNet's first six senses, all nouns
    assert "become less intense" not in say(app, "SENSES flag")
    out = say(app, "SENSES flag --all")
    rows = numbered(out)
    assert sorted(rows) == list(range(1, 13))  # WordNet's 12 senses of 'flag'
    assert rows[12] == "  12   verb: become less intense"
    assert "More from wordnet (saved only if you choose one):" in out
    assert len(app.storage.load_words("Book").words["flag"].senses) == 6  # nothing saved


def test_senses_all_keeps_every_sense_the_dictionary_lists_twice(app):
    real_nltk.require("corpora/wordnet.zip")
    app.config.active_dictionary = "wordnet"
    # WordNet lists 'hinder' (the adjective) twice, word for word.
    app.cmd_add("hinder")
    out = say(app, "SENSES hinder --all")
    assert sorted(numbered(out)) == [1, 2, 3, 4, 5]
    assert "('wordnet' has no other senses of 'hinder'.)" in out


def test_senses_all_offers_a_repeated_sense_that_was_not_saved(app, monkeypatch):
    import vocab.cli.app as app_module
    repeated = THREE + [Sense("decorate with flags", "verb")]
    looked_up(app, monkeypatch)
    app.cmd_add("flag")
    monkeypatch.setattr(app_module, "get_dictionary", lambda name: Dictionary(repeated))
    rows = numbered(say(app, "SENSES flag --all"))
    assert rows[4] == "   4   verb: decorate with flags"


@pytest.mark.parametrize("problem, note", [
    ("unknown", "Its dictionary 'test' is not one this version knows"),
    ("unavailable", "'wordnet' is not available here, so its other senses cannot be listed"),
    ("failing", "Could not list the other senses from 'wordnet': offline right now")])
def test_senses_all_without_its_dictionary_still_lists_the_saved_senses(
        app, monkeypatch, problem, note):
    from vocab.dictionaries import LookupFailed
    from vocab.dictionaries.wordnet import WordNet
    looked_up(app, monkeypatch, dictionary="test" if problem == "unknown" else "wordnet")
    app.cmd_add("flag")
    monkeypatch.setattr(WordNet, "available", lambda self: problem != "unavailable")

    def failing(self, word):
        raise LookupFailed("offline right now", recoverable=True)
    monkeypatch.setattr(WordNet, "lookup", failing)
    out = say(app, "SENSES flag --all")
    assert sorted(numbered(out)) == [1, 2, 3]
    assert f"({note}" in out


def test_the_add_reply_points_to_senses(app, monkeypatch):
    looked_up(app, monkeypatch)
    app.cmd_create("Moby Dick")
    assert say(app, "ADD flag").splitlines()[-1].endswith(
        "2 more senses saved.  Others:  SENSES flag")
    assert say(app, "ADD ice cream").splitlines()[-1].endswith('Others:  SENSES "ice cream"')
    assert say(app, 'ADDL "Moby Dick" whale').splitlines()[-1].endswith(
        'Others:  USE "Moby Dick"  then  SENSES whale')
    from vocab.cli.help_text import HELP
    assert "SENSES <word> [--all]" in HELP


# ---- SENSE chooses ---------------------------------------------------------
def senses_of(app, word, module="Book"):
    return [s["definition"] for s in raw(app, word, module)["senses"]]


def test_sense_switches_to_the_real_wordnet_sense_and_keeps_progress(app, monkeypatch):
    real_nltk.require("corpora/wordnet.zip")
    app.config.active_dictionary = "wordnet"
    tag_as(monkeypatch, "v")
    app.cmd_add("flag", sentence=VERB_USE)
    assert senses_of(app, "flag")[0] == "communicate or signal with a flag"  # a wrong guess
    stats = app.storage.load_stats("Book")
    stats["flag"]["box"] = 3
    app.storage.save_stats("Book", stats)
    progress = app.paths.stats_file("Book").read_bytes()
    listing = say(app, "SENSES flag")
    number = next(n for n, row in numbered(listing).items() if "become less intense" in row)
    before = senses_of(app, "flag")
    reply = say(app, f"SENSE {number} flag")
    assert reply.splitlines()[0] == ("'flag' in 'Book' now studies: verb: become less intense"
                                     "  (its progress is kept)")
    assert numbered(reply)[1] == "   1 * verb: become less intense"
    after = senses_of(app, "flag")
    # Moved to the front; the others keep their order and nothing is dropped.
    assert after == [before[number - 1]] + before[:number - 1] + before[number:]
    assert app.paths.stats_file("Book").read_bytes() == progress
    assert raw(app, "flag")["context"] == VERB_USE and raw(app, "flag")["choice"] == "user"
    assert "First sense chosen by you." in say(app, "SENSES flag")
    assert "become less intense" in say(app, "LS")


def test_sense_can_choose_a_sense_listed_by_all(app):
    real_nltk.require("corpora/wordnet.zip")
    app.config.active_dictionary = "wordnet"
    app.cmd_add("flag")
    say(app, "SENSES flag --all")
    reply = say(app, "SENSE 12 flag")
    assert "Saved from wordnet; 'flag' now has 7 senses." in reply
    word = app.storage.load_words("Book").words["flag"]
    assert word.primary_definition() == "become less intense" and len(word.senses) == 7
    assert say(app, "SENSE 9 flag").startswith(  # the list now holds the 7 saved senses
        "! 'flag' has 7 senses to choose from; choose 1 to 7. SENSES flag --all lists")


def test_sense_never_drops_senses_that_read_the_same(app):
    real_nltk.require("corpora/wordnet.zip")
    app.config.active_dictionary = "wordnet"
    # WordNet's 'occasional' has two senses "occurring from time to time",
    # each with its own example.
    app.cmd_add("occasional")
    entry = raw(app, "occasional")
    say(app, "SENSES occasional")
    say(app, "SENSE 1 occasional")  # the first sense: only marked as chosen
    assert raw(app, "occasional") == {**entry, "choice": "user"}
    say(app, "SENSE 3 occasional")
    examples = [s["example"] for s in raw(app, "occasional")["senses"]]
    assert sorted(examples) == sorted(s["example"] for s in entry["senses"])
    assert examples[0] == entry["senses"][2]["example"]
    before = app.paths.words_file("Book").read_bytes()
    assert "(nothing was changed)" in say(app, "SENSE 1 occasional")
    assert app.paths.words_file("Book").read_bytes() == before


def test_sense_refuses_numbers_from_a_listing_another_window_reordered(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag")
    say(app, "SENSES flag")  # 2 = become less intense
    other = App(tmp_path / "home")
    other.cmd_use("Book")
    assert not say(other, "SENSE 3 flag").startswith("!")  # now 2 = the emblem
    before = app.paths.words_file("Book").read_bytes()
    reply = say(app, "SENSE 2 flag")
    assert reply == ("! 'flag' changed since its senses were listed (in another window or "
                     "program), so nothing was changed. List them again:  SENSES flag")
    assert app.paths.words_file("Book").read_bytes() == before
    say(app, "SENSES flag")
    assert not say(app, "SENSE 3 flag").startswith("!")  # listed again, it works
    assert senses_of(app, "flag")[0] == "become less intense"


def test_sense_refuses_when_the_word_changed_since_it_was_listed(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=VERB_USE)
    say(app, "SENSES flag")
    App(tmp_path / "home").cmd_add("flag", target="Book", manual_def="my own meaning")
    before = app.paths.words_file("Book").read_bytes()
    assert say(app, "SENSE 2 flag").startswith("! 'flag' changed since its senses were listed")
    assert app.paths.words_file("Book").read_bytes() == before
    App(tmp_path / "home").cmd_delete_word("flag", target="Book")
    assert say(app, "SENSE 2 flag") == "! 'flag' is no longer in 'Book', so nothing was changed."


def test_sense_refuses_a_module_recreated_under_the_same_name(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag")
    say(app, "SENSES flag")
    other = App(tmp_path / "home")
    looked_up(other, monkeypatch)
    other.cmd_delete("Book")
    other.cmd_create("Book")
    other.cmd_add("flag", target="Book")  # the same entry, in a new module
    before = app.paths.words_file("Book").read_bytes()
    assert say(app, "SENSE 2 flag").startswith("! 'flag' changed since its senses were listed")
    assert app.paths.words_file("Book").read_bytes() == before


def test_sense_refuses_a_listing_from_a_deleted_module(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag")
    listing = app.sense_listing("Book", "flag")
    App(tmp_path / "home").cmd_delete("Book")
    with pytest.raises(ValueError, match="'Book' no longer exists, so nothing was changed."):
        app.choose_senses(listing, [1])


def test_sense_leaves_a_manual_definition_alone(app):
    app.cmd_add("ephemeral", manual_def="my careful definition")
    before = app.paths.words_file("Book").read_bytes()
    assert say(app, "SENSE 1 ephemeral") == (
        "! 'ephemeral' has your own definition, so there are no dictionary senses to choose.")
    assert app.paths.words_file("Book").read_bytes() == before


@pytest.mark.parametrize("numbers, message", [
    ("0", "! Sense numbers start at 1."),
    ("4", "! 'flag' has 3 senses to choose from; choose 1 to 3. SENSES flag --all lists"),
    ("2,2", "! Give each sense number once."),
    ("1,,2", "! Write sense numbers like 2"),
    (",", "! Write sense numbers like 2")])
def test_sense_rejects_numbers_that_do_not_name_listed_senses(app, monkeypatch, numbers, message):
    looked_up(app, monkeypatch)
    app.cmd_add("flag")
    before = app.paths.words_file("Book").read_bytes()
    assert say(app, f"SENSE {numbers} flag").startswith(message)
    assert app.paths.words_file("Book").read_bytes() == before


def test_sense_numbers_choose_and_engine_names_still_switch_the_engine(app, monkeypatch):
    looked_up(app, monkeypatch)
    app.cmd_add("flag")
    reply = say(app, "SENSE 3 flag")
    assert "now studies: verb: decorate with flags" in reply
    assert reply.endswith("(No SENSES list was shown here, so the number followed the saved order.)")
    assert say(app, "SENSE base") == "Sense selection set to base."  # the old alias
    assert say(app, "SENSE NLP") == "Sense selection set to nlp."
    for line in ("SENSE flag", "SENSE", "SENSE nlp flag"):  # usage, not "Unknown engine"
        assert say(app, line).startswith("Usage: SENSE <n> <word>"), line
    from vocab.cli.help_text import HELP
    assert "SENSE <n> <word>" in HELP


def test_a_multiword_term_chooses_by_number(app, monkeypatch):
    looked_up(app, monkeypatch)
    app.cmd_add("ice cream")
    say(app, 'SENSES "ice cream"')
    assert "now studies: verb: become less intense" in say(app, "SENSE 2 ice cream")


def test_a_lookup_that_started_before_a_choice_keeps_the_choice(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=VERB_USE)
    other = App(tmp_path / "home")
    other.cmd_use("Book")

    def choose_meanwhile(word, sentence=""):
        assert not say(other, "SENSE 3 flag").startswith("!")
        return list(THREE), "wordnet", ""
    monkeypatch.setattr(app, "_lookup_senses", choose_meanwhile)
    assert "already in 'Book'" in app.cmd_add("flag", sentence="They raised the flag.")
    assert senses_of(app, "flag")[0] == "decorate with flags"
    assert raw(app, "flag")["context"] == VERB_USE


def test_import_rejects_a_choice_that_is_not_text(app, tmp_path):
    entry = {"senses": [{"definition": "a"}], "choice": 1}
    source = tmp_path / "in.json"
    source.write_text(json.dumps({"name": "In", "words": {"w": entry}}), encoding="utf-8")
    with pytest.raises(ValueError, match="'choice' must be text"):
        app.cmd_import(str(source), None, with_stats=False)


def test_export_and_import_keep_the_choice(app, monkeypatch, tmp_path):
    looked_up(app, monkeypatch)
    app.cmd_add("flag", sentence=VERB_USE)
    say(app, "SENSE 3 flag")
    say(app, f"EXPORT Book {tmp_path / 'out.json'}")
    say(app, f"IMPORT {tmp_path / 'out.json'} as Copy")
    assert raw(app, "flag", "Copy") == raw(app, "flag")
    assert raw(app, "flag", "Copy")["choice"] == "user"
