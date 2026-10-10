"""DOCTOR and install hints describe the runtime they run in (L10)."""
import sys
import types

import pytest

from vocab import nltk_resources
from vocab.cli.app import App
from vocab.dictionaries import REGISTRY
from vocab.dictionaries.base import LookupFailed
from vocab.dictionaries.wordnet import WordNet

# Advice that only makes sense for a source install.
SOURCE_ADVICE = ("pip", "venv", "virtualenv", "interpreter", "dict install")


@pytest.fixture(autouse=True)
def fresh_tagger_check(monkeypatch):
    monkeypatch.setattr(nltk_resources, "_tagger_works", False)


def _nltk(monkeypatch, corpus=True):
    """Install a stand-in nltk (with a working tagger) whose WordNet corpus is
    present or missing."""
    def ensure_loaded():
        if not corpus:
            raise LookupError("Resource wordnet not found.")
    wordnet = types.SimpleNamespace(ensure_loaded=ensure_loaded,
                                    synsets=lambda word: ["test.n.01"] if corpus else [])
    nltk = types.ModuleType("nltk")
    nltk.__version__ = "3.9.1"
    nltk.download = lambda *args, **kwargs: True      # never touches the network
    nltk.pos_tag = lambda tokens: [(token, "NN") for token in tokens]
    nltk.corpus = types.ModuleType("nltk.corpus")
    nltk.corpus.wordnet = wordnet
    monkeypatch.setitem(sys.modules, "nltk", nltk)
    monkeypatch.setitem(sys.modules, "nltk.corpus", nltk.corpus)


def _no_nltk(monkeypatch):
    for name in ("nltk", "nltk.corpus"):
        monkeypatch.setitem(sys.modules, name, None)    # import fails


def _runtime(monkeypatch, kind):
    if kind == "frozen":                 # what PyInstaller's bootloader provides
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "base_prefix", sys.prefix)
        monkeypatch.setattr(sys, "executable", r"C:\Apps\VocabStudy\VocabStudy.exe")
    else:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(sys, "base_prefix", sys.prefix if kind == "system" else "/usr")


RUNTIME_LINE = {"frozen": "Runtime     : packaged app",
                "system": "Virtualenv  : no",     # a valid source install need not be a venv
                "venv": "Virtualenv  : yes"}


@pytest.mark.parametrize("kinds", [("frozen",), ("system", "venv")], ids=["packaged", "source"])
def test_doctor_states_runtime_facts_for_a_working_dictionary(tmp_path, monkeypatch, kinds):
    _nltk(monkeypatch)
    for kind in kinds:
        _runtime(monkeypatch, kind)
        out = App(tmp_path / kind).cmd_doctor()
        assert RUNTIME_LINE[kind] in out.splitlines()
        assert "OK: using the offline dictionary" in out
        assert "wrong interpreter" not in out and "PROBLEM" not in out
        if kind == "frozen":
            assert "Virtualenv" not in out
            assert not any(advice in out.lower() for advice in SOURCE_ADVICE)


@pytest.mark.parametrize("damage", ["no-nltk", "no-corpus"])
def test_doctor_repair_advice_matches_runtime(tmp_path, monkeypatch, damage):
    if damage == "no-nltk":
        _no_nltk(monkeypatch)
    else:
        _nltk(monkeypatch, corpus=False)
    _runtime(monkeypatch, "venv")
    source = App(tmp_path / "source").cmd_doctor()
    _runtime(monkeypatch, "frozen")
    packaged = App(tmp_path / "packaged").cmd_doctor()
    assert "PROBLEM" in source and "PROBLEM" in packaged
    expected = 'pip install "vocab-study[wordnet]"' if damage == "no-nltk" else "dict install wordnet"
    assert expected in source
    assert not any(advice in packaged.lower() for advice in SOURCE_ADVICE)
    assert "missing its built-in WordNet" in packaged and "fresh copy" in packaged
    assert "_internal" in packaged


def test_packaged_tagger_note_offers_the_packaged_repair(tmp_path, monkeypatch):
    _nltk(monkeypatch)
    _runtime(monkeypatch, "frozen")
    monkeypatch.setattr(nltk_resources, "pos_tagger_problem", lambda: nltk_resources.TaggerProblem(
        "the NLTK data package 'averaged_perceptron_tagger_eng' is not installed", "no-data"))
    app = App(tmp_path)
    doctor, disambig = app.cmd_doctor(), app.cmd_disambig("")
    assert doctor.split("Note:", 1)[1].rstrip().endswith("beside the VocabStudy program.")
    assert "fresh copy" in disambig
    for text in (doctor, disambig):
        assert not any(advice in text.lower() for advice in SOURCE_ADVICE)


def test_packaged_install_failures_do_not_prescribe_pip(tmp_path, monkeypatch):
    _runtime(monkeypatch, "frozen")
    _nltk(monkeypatch, corpus=False)
    message = App(tmp_path).cmd_dict_install("wordnet")
    assert "still unavailable" in message and "fresh copy" in message
    assert "pip" not in message
    _no_nltk(monkeypatch)
    with pytest.raises(LookupFailed) as failure:
        WordNet().install()
    assert "fresh copy" in str(failure.value) and "pip" not in str(failure.value)
    # A source install still gets the pip advice.
    _runtime(monkeypatch, "venv")
    with pytest.raises(LookupFailed, match='pip install "vocab-study'):
        WordNet().install()


@pytest.mark.parametrize("kind, corpus, advice", [
    ("venv", True, "For offline lookups:  dict use wordnet"),
    ("venv", False, "For offline lookups:  dict install wordnet  then  dict use wordnet"),
    ("frozen", False, "missing its built-in WordNet.\nExtract a fresh copy"),
], ids=["wordnet-ready", "wordnet-missing", "packaged-without-wordnet"])
def test_choosing_the_online_dictionary_is_not_a_problem(tmp_path, monkeypatch, kind, corpus,
                                                         advice):
    for other in set(REGISTRY) - {"freedict", "wordnet"}:  # e.g. test_vocab's mock
        monkeypatch.delitem(REGISTRY, other)
    _nltk(monkeypatch, corpus=corpus)
    _runtime(monkeypatch, kind)
    app = App(tmp_path)
    app.config.active_dictionary = "freedict"  # DICT USE freedict
    out = app.cmd_doctor()
    assert "PROBLEM" not in out and "unavailable here" not in out
    assert "OK: using the online dictionary you chose ('freedict')" in out
    assert advice in out
    if kind == "frozen":
        assert not any(word in out.lower() for word in SOURCE_ADVICE)
