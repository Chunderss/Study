"""Sense selection tags the part of speech offline with the data the app ships (M4)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from tests import real_nltk
from vocab import nltk_resources
from vocab.cli.app import App
from vocab.cli.parser import parse
from vocab.cli.repl import Repl
from vocab.core.models import Sense
from vocab.dictionaries.wordnet import WordNet
from vocab.nltk_resources import NLTK_PACKAGES, POS_TAGGER, TaggerProblem
from vocab.study.disambiguate import NlpDisambiguator

ROOT = Path(__file__).resolve().parents[1]
TAGGER_FIX = "dict install wordnet"
PIP_FIX = 'pip install "vocab-study[wordnet]"'

# Runs in a child interpreter that sees only the given data folder, so neither a
# user's ~/nltk_data nor NLTK's in-process tagger cache can hide missing data.
CHILD = r"""
import json, sys
import nltk
nltk.data.path[:] = [sys.argv[1]]
downloads = []
nltk.download = lambda *a, **k: downloads.append(a) or False
from vocab.cli.app import App
from vocab.core.models import Sense
from vocab.study.disambiguate import NlpDisambiguator
nlp = NlpDisambiguator()
out = {"pos": [nlp._wn_pos_of(w, s) for w, s in json.loads(sys.argv[3])]}
bank = [Sense("sloping land beside a body of water", "noun"),
        Sense("a financial institution that accepts deposits", "noun")]
out["lesk"] = nlp.rank("bank", "the bank accepts deposits", bank)[0].definition
app = App(root=sys.argv[2])
app.config.active_dictionary = "wordnet"
out["doctor"], out["disambig"] = app.cmd_doctor(), app.cmd_disambig("")
saved = {}
app.cmd_create("Book")
for sentence in ("His energy began to flag.", "They raised the flag over the fort."):
    app.cmd_add("flag", target="Book", sentence=sentence)
    words = app.storage.load_words("Book")
    saved[sentence] = words.words[words.normalize_key("flag")].senses[0].pos
    app.cmd_delete_word("flag", "Book")
out["saved"], out["downloads"] = saved, downloads
print(json.dumps(out))
"""

# (word, sentence) -> the WordNet part of speech the tagger should give.
USES = [
    (("flag", "His energy began to flag."), "v"),
    (("flagged", "The building was flagged for the holiday."), "v"),
    (("runs", "She runs the company."), "v"),
    (("Ephemeral", "Ephemeral fame fades."), "a"),
    (("quickly", "He ran quickly home."), "r"),
    (("table", "They voted to table the motion."), "v"),
    (("flag", "They raised the flag over the fort."), "n"),
    # Punctuation the tokenizer leaves attached to the word.
    (("flag", "His energy began to flag…"), "v"),
    (("flag", "His energy began to flag. Then he slept."), "v"),
    # Nothing to tag: a multi-word term (no single token), the word missing
    # from the sentence, and empty input.
    (("give up", "Never give up."), None),
    (("zebra", "His energy began to flag."), None),
    (("", "His energy began to flag."), None),
    (("flag", ""), None),
]


def run_child(tmp_path, data):
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(data), str(tmp_path / "home"),
         json.dumps([use for use, _ in USES])],
        cwd=ROOT, env=real_nltk.isolated_env(tmp_path), capture_output=True, text=True,
        timeout=120)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def test_bundled_packages_alone_infer_the_part_of_speech(tmp_path):
    data = real_nltk.bundle_copy(tmp_path / "nltk_data")  # wordnet + tagger zips, no Punkt
    out = run_child(tmp_path, data)
    assert out["pos"] == [pos for _, pos in USES]
    # The part of speech decides which senses compete. Which verb sense wins is
    # still a guess by definition overlap ("communicate or signal with a flag",
    # not "decline in vigor"), so only the part of speech is asserted.
    assert out["saved"] == {"His energy began to flag.": "verb",
                            "They raised the flag over the fort.": "noun"}
    assert "POS tagger  : present" in out["doctor"].splitlines()
    assert "Note:" not in out["doctor"] and "note" not in out["disambig"]
    assert out["downloads"] == []


def test_wordnet_without_the_tagger_is_reported_and_never_downloaded(tmp_path):
    # What the packaged app shipped before, and every older source install.
    data = real_nltk.bundle_copy(tmp_path / "nltk_data", packages=["wordnet"])
    out = run_child(tmp_path, data)
    assert out["pos"] == [None] * len(USES)
    assert out["lesk"].startswith("a financial institution")  # ranking still works
    # Without a part of speech the dictionary's first senses keep their lead.
    assert out["saved"] == {"His energy began to flag.": "noun",
                            "They raised the flag over the fort.": "noun"}
    doctor = out["doctor"]
    assert "OK: using the offline dictionary. No network needed." in doctor
    assert "WordNet data: present" in doctor and "POS tagger  : MISSING" in doctor
    note = doctor.split("Note:", 1)[1]
    assert POS_TAGGER in note and "overlap" in note and note.rstrip().endswith(TAGGER_FIX)
    assert POS_TAGGER in out["disambig"] and f"(fix: {TAGGER_FIX})" in out["disambig"]
    assert out["downloads"] == []


@pytest.mark.skipif(os.name == "nt", reason="NLTK refuses hard-linked data files on POSIX only")
def test_unreadable_tagger_names_the_cause(tmp_path):
    data = real_nltk.bundle_copy(tmp_path / "nltk_data")
    tagger = data / NLTK_PACKAGES[POS_TAGGER]
    os.link(tagger, tmp_path / "second-link.zip")  # NLTK 3.10 refuses st_nlink > 1
    out = run_child(tmp_path, data)
    if "POS tagger  : present" in out["doctor"]:
        pytest.skip("this NLTK reads hard-linked data files")
    assert "POS tagger  : UNUSABLE" in out["doctor"]
    note = out["doctor"].split("Note:", 1)[1]
    assert "cannot be used (PermissionError" in note
    assert TAGGER_FIX not in note  # downloading again would not help
    assert out["pos"] == [None] * len(USES) and out["downloads"] == []


@pytest.fixture
def no_nltk(monkeypatch):
    for name in ("nltk", "nltk.corpus"):
        monkeypatch.setitem(sys.modules, name, None)  # import fails
    monkeypatch.setattr(nltk_resources, "_tagger_works", False)


def test_missing_nltk_gives_the_pip_fix_not_dict_install(tmp_path, no_nltk):
    app = App(root=str(tmp_path))
    app.config.active_dictionary = "wordnet"
    doctor, disambig = app.cmd_doctor(), app.cmd_disambig("")
    assert "POS tagger  : MISSING" in doctor
    note = doctor.split("Note:", 1)[1]
    assert "nltk is not installed" in note and note.rstrip().endswith(PIP_FIX)
    assert f"(fix: {PIP_FIX})" in disambig
    assert TAGGER_FIX not in doctor and TAGGER_FIX not in disambig
    assert WordNet().needs_install()
    # Ranking by definition overlap needs no nltk.
    bank = [Sense("sloping land beside a body of water", "noun"),
            Sense("a financial institution that accepts deposits", "noun")]
    ranked = NlpDisambiguator().rank("bank", "the bank accepts deposits", bank)
    assert ranked[0] is bank[1]


@pytest.fixture
def downloads(monkeypatch):
    """WordNet works; the tagger is missing until a download brings it."""
    nltk = pytest.importorskip("nltk")
    fetched, tagger = [], {"arrives": True}

    def download(package, **kwargs):
        fetched.append(package)
        return True

    def problem():
        if tagger["arrives"] and POS_TAGGER in fetched:
            return None
        return TaggerProblem(f"the NLTK data package '{POS_TAGGER}' is not installed", "no-data")

    monkeypatch.setattr(nltk, "download", download)
    monkeypatch.setattr(WordNet, "available", lambda self: True)
    monkeypatch.setattr(nltk_resources, "pos_tagger_problem", problem)
    return fetched, tagger


def test_installing_wordnet_installs_the_tagger_too(tmp_path, downloads):
    fetched, tagger = downloads
    app = App(root=str(tmp_path))
    # WordNet itself works, but the tagger is missing: installing is not a no-op.
    assert app.cmd_dict_install("wordnet") == "Installed 'wordnet' (~1.5 MB (part-of-speech tagger))."
    assert fetched == list(NLTK_PACKAGES) == ["wordnet", POS_TAGGER]
    # A download that still leaves tagging broken is not reported as a success.
    fetched.clear()
    tagger["arrives"] = False
    message = app.cmd_dict_install("wordnet")
    assert message.startswith("Installed 'wordnet', but part-of-speech tagging still does not work")
    assert POS_TAGGER in message


def test_dict_install_asks_before_fetching_a_missing_tagger(tmp_path, downloads):
    fetched, _ = downloads
    repl = Repl(App(root=str(tmp_path)))
    asked = []
    repl._confirm = lambda text: asked.append(text) or False
    assert repl.dispatch(parse("DICT INSTALL wordnet")) == "Cancelled."
    assert asked == ["'wordnet' needs ~1.5 MB (part-of-speech tagger). Download now?"]
    assert fetched == []


def _build_script(monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))  # the script extends it
    spec = importlib.util.spec_from_file_location("build_desktop", ROOT / "tools" / "build_desktop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_downloads_and_verifies_the_tagger(tmp_path, monkeypatch):
    build = _build_script(monkeypatch)
    requested = []

    def download(package, download_dir, **kwargs):
        requested.append(package)
        bundled = Path(download_dir) / NLTK_PACKAGES[package]
        bundled.parent.mkdir(parents=True, exist_ok=True)
        bundled.write_bytes(real_nltk.require(NLTK_PACKAGES[package]).read_bytes())
        return True

    build.provision_nltk_data(tmp_path / "ok" / "nltk_data", download=download)
    assert requested == ["wordnet", POS_TAGGER]
    # A download that silently leaves the tagger out fails the build.
    skip_tagger = lambda package, **kw: package != "wordnet" or download(package, **kw)
    with pytest.raises(SystemExit, match="verification failed"):
        build.provision_nltk_data(tmp_path / "no_tagger" / "nltk_data", download=skip_tagger)


def test_build_verifies_the_zip_that_ships(tmp_path, monkeypatch):
    # nltk.download also unzips the tagger beside its zip, and NLTK prefers the
    # unzipped folder; the spec ships only the zip, so that is what must work.
    build = _build_script(monkeypatch)
    corpus = real_nltk.bundle_copy(tmp_path / "nltk_data")
    tagger = corpus / NLTK_PACKAGES[POS_TAGGER]
    with zipfile.ZipFile(tagger) as archive:
        archive.extractall(tagger.parent)
    build.verify_nltk_data(corpus)
    tagger.write_bytes(b"damaged")
    with pytest.raises(SystemExit, match="verification failed"):
        build.provision_nltk_data(corpus, download=lambda *a, **k: pytest.fail("downloaded"))


def _run_spec(tmp_path, monkeypatch, hooked=()):
    """Run VocabStudy.spec with PyInstaller stand-ins and return the datas it
    hands to COLLECT. Like Analysis, the stand-in lists the spec's own datas
    first, then what hooks add (``hooked``), and keeps the first entry for
    each destination."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # the spec extends it
    collected = []

    def analysis(*args, datas=(), **kwargs):
        toc = [(os.path.join(dest, Path(src).name), src, "DATA") for src, dest in datas]
        taken = {os.path.normcase(os.path.normpath(entry[0])) for entry in toc}
        toc += [entry for entry in hooked
                if os.path.normcase(os.path.normpath(entry[0])) not in taken]
        return type("A", (), {"pure": [], "scripts": [], "binaries": [], "datas": toc})()

    names = dict(SPECPATH=str(tmp_path), PYZ=lambda *a, **k: None, EXE=lambda *a, **k: None,
                 COLLECT=lambda exe, binaries, datas, **k: collected.extend(datas),
                 Analysis=analysis)
    source = (ROOT / "VocabStudy.spec").read_text(encoding="utf-8")
    exec(compile(source, "VocabStudy.spec", "exec"), names)
    return collected


def _shipped_nltk_data(datas):
    return sorted((Path(dest).as_posix(), Path(src)) for dest, src, kind in datas
                  if Path(dest).parts[0] == "nltk_data")


def test_spec_bundles_the_tagger(tmp_path, monkeypatch):
    corpus = tmp_path / "build" / "nltk_data"
    (corpus / "corpora").mkdir(parents=True)
    (corpus / "corpora" / "wordnet.zip").write_bytes(b"zip")
    with pytest.raises(SystemExit, match=POS_TAGGER):
        _run_spec(tmp_path, monkeypatch)
    (corpus / "taggers" / POS_TAGGER).mkdir(parents=True)  # an unzipped copy is not shipped
    (corpus / "taggers" / f"{POS_TAGGER}.zip").write_bytes(b"zip")
    assert _shipped_nltk_data(_run_spec(tmp_path, monkeypatch)) == [
        ("nltk_data/corpora/wordnet.zip", corpus / "corpora" / "wordnet.zip"),
        (f"nltk_data/taggers/{POS_TAGGER}.zip", corpus / "taggers" / f"{POS_TAGGER}.zip")]


def test_spec_ships_no_other_nltk_data_from_the_build_machine(tmp_path, monkeypatch):
    # PyInstaller's nltk hook adds every NLTK data folder on the build machine,
    # e.g. ~/nltk_data holding a tagger nltk.download unzipped (which NLTK would
    # read before the verified zip), a second WordNet zip and other packages.
    corpus = tmp_path / "build" / "nltk_data"
    for bundled in NLTK_PACKAGES.values():
        (corpus / bundled).parent.mkdir(parents=True, exist_ok=True)
        (corpus / bundled).write_bytes(b"zip")
    builder = tmp_path / "builder" / "nltk_data"
    hooked = [(os.path.join("nltk_data", *name.split("/")), str(builder / name), "DATA")
              for name in ("corpora/wordnet.zip", f"taggers/{POS_TAGGER}/{POS_TAGGER}.weights.json",
                           f"taggers/{POS_TAGGER}.zip", "tokenizers/punkt_tab/english/collocations.tab")]
    # The rest of what Analysis collects ships unchanged, nltk's own files included.
    others = [(os.path.join("nltk", "VERSION"), str(tmp_path / "site" / "nltk" / "VERSION"), "DATA"),
              ("base_library.zip", str(tmp_path / "work" / "base_library.zip"), "DATA")]
    shipped = _run_spec(tmp_path, monkeypatch, hooked + others)
    assert [entry for entry in shipped if entry in others] == others
    assert _shipped_nltk_data(shipped) == [
        ("nltk_data/corpora/wordnet.zip", corpus / "corpora" / "wordnet.zip"),
        (f"nltk_data/taggers/{POS_TAGGER}.zip", corpus / "taggers" / f"{POS_TAGGER}.zip")]
    assert len(shipped) == len(others) + len(NLTK_PACKAGES)
