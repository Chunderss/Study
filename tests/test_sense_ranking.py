"""Senses are ranked for their context before the list is trimmed (M5)."""
import io
import json
import urllib.request

import pytest

from tests import real_nltk
from vocab.cli.app import App
from vocab.core.models import Sense
from vocab.dictionaries.freedict import FreeDictionary
from vocab.dictionaries.wordnet import WordNet
from vocab.study.disambiguate import NlpDisambiguator, OllamaDisambiguator

# Shaped like WordNet's 'flag': seven noun senses, then five verb senses. Only
# the verb glosses repeat the word, as "communicate or signal with a flag" does.
NOUNS = [f"noun sense {i}" for i in range(7)]
VERBS = [f"verb sense {i}, done with a flag" for i in range(5)]
VERB_USE = "His energy began to flag."
NOUN_USE = "They raised the flag over the fort."


class _Synset:
    def __init__(self, definition, pos):
        self._definition, self._pos = definition, pos

    def definition(self):
        return self._definition

    def pos(self):
        return self._pos

    def examples(self):
        return []


class _Corpus:
    def synsets(self, word):
        return [_Synset(d, "n") for d in NOUNS] + [_Synset(d, "v") for d in VERBS]


def _payload():
    return json.dumps([{"meanings": [
        {"partOfSpeech": "noun", "definitions": [{"definition": d} for d in NOUNS]},
        {"partOfSpeech": "verb", "definitions": [{"definition": d} for d in VERBS]},
    ]}]).encode()


def _tag_as(monkeypatch, wn_pos):
    """The tagger reads the word as wn_pos ('v', or None: no part of speech)."""
    monkeypatch.setattr(NlpDisambiguator, "_ensure_tagger", lambda self: True)
    monkeypatch.setattr(NlpDisambiguator, "_wn_pos_of", lambda self, word, sentence: wn_pos)


def test_wordnet_lookup_returns_every_sense(monkeypatch):
    monkeypatch.setattr(WordNet, "_corpus", lambda self: _Corpus())
    senses = WordNet().lookup("flag")
    assert [s.definition for s in senses] == NOUNS + VERBS


def test_freedict_returns_every_sense():
    senses = FreeDictionary._parse(json.loads(_payload()))
    assert [(s.pos, s.definition) for s in senses] == \
        [("noun", d) for d in NOUNS] + [("verb", d) for d in VERBS]


@pytest.mark.parametrize("dictionary", ["wordnet", "freedict"])
def test_context_ranking_sees_senses_after_the_first_six(tmp_path, monkeypatch, dictionary):
    monkeypatch.setattr(WordNet, "_corpus", lambda self: _Corpus())
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda request, timeout=None: io.BytesIO(_payload()))
    app = App(root=str(tmp_path))
    app.config.active_dictionary = dictionary
    _tag_as(monkeypatch, "v")
    senses, used, _ = app._lookup_senses("flag", VERB_USE)
    assert used == dictionary
    # The verbs come first; the list is still trimmed to six.
    assert [s.definition for s in senses] == VERBS + NOUNS[:1]
    # Without a sentence nothing is ranked: the dictionary's first six, as before.
    senses, _, _ = app._lookup_senses("flag")
    assert [s.definition for s in senses] == NOUNS[:6]
    # Without a part of speech (no tagger), a later gloss that merely repeats
    # the word must not displace the first senses.
    _tag_as(monkeypatch, None)
    senses, _, _ = app._lookup_senses("flag", NOUN_USE)
    assert [s.definition for s in senses] == NOUNS[:6]


def test_ollama_prompt_offers_the_ranked_senses_within_a_limit(monkeypatch):
    _tag_as(monkeypatch, "v")
    # 'run' has 16 noun senses before its 41 verb senses.
    senses = ([Sense(f"run noun {i}", "noun") for i in range(16)]
              + [Sense(f"run verb {i}", "verb") for i in range(41)])
    prompts = []

    def urlopen(request, timeout=None):
        prompts.append(json.loads(request.data)["prompt"])
        return io.BytesIO(json.dumps({"response": "2"}).encode())

    monkeypatch.setattr(OllamaDisambiguator, "_server_up", lambda self, use_cache=True: True)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    ranked = OllamaDisambiguator(fallback=NlpDisambiguator()).rank(
        "run", "She will run the company.", senses)
    offered = [line for line in prompts[0].splitlines() if line[:1].isdigit()]
    assert len(offered) == 12  # OllamaDisambiguator.PROMPT_SENSES, not all 57
    assert all("(verb)" in line for line in offered)
    assert ranked[0].definition == "run verb 2"  # the model's choice, mapped back
    assert sorted(s.definition for s in ranked) == sorted(s.definition for s in senses)


def test_real_wordnet_lookup_keeps_every_sense():
    real_nltk.require("corpora/wordnet.zip")
    from nltk.corpus import wordnet
    senses = WordNet().lookup("flag")
    assert [s.pos for s in senses] == ["noun"] * 7 + ["verb"] * 5
    assert [s.definition for s in senses] == [syn.definition() for syn in wordnet.synsets("flag")]


def test_real_wordnet_senses_follow_the_part_of_speech(tmp_path, monkeypatch):
    real_nltk.require("corpora/wordnet.zip")
    from nltk.corpus import wordnet
    app = App(root=str(tmp_path))
    app.config.active_dictionary = "wordnet"
    _tag_as(monkeypatch, "v")
    senses, _, _ = app._lookup_senses("flag", VERB_USE)
    assert [s.pos for s in senses] == ["verb"] * 5 + ["noun"]
    # With no part of speech (WordNet without the tagger), the first sense keeps
    # its lead. Over all senses, Lesk would pick "communicate or signal with a
    # flag" and, for 'break', "reduce to bankruptcy".
    _tag_as(monkeypatch, None)
    for word, sentence in (("flag", NOUN_USE), ("break", "Let's take a break.")):
        senses, _, _ = app._lookup_senses(word, sentence)
        assert senses[0].pos == "noun", (word, senses[0])
        first_six = {syn.definition() for syn in wordnet.synsets(word)[:6]}
        assert {s.definition for s in senses} == first_six
