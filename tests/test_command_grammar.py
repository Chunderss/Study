r"""One command grammar: Windows paths, double quotes, '::' bodies.

Dispatch-level: each line goes through parse() and Repl.dispatch() (or the
whole REPL loop), exactly as typed in the terminal or the desktop Console.
Backslashes and single quotes are ordinary characters; only a double quote
at the start of an argument groups it.
"""
import io
import json
import re
import sys

import pytest

from vocab.cli.app import App
from vocab.cli.parser import parse
from vocab.cli.repl import Repl


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    app.cmd_use("Book")
    return app


def run(app, line):
    return Repl(app, in_stream=io.StringIO(), out_stream=io.StringIO()).dispatch(parse(line))


def loop(app, *lines):
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO("\n".join(lines + ("QUIT",)) + "\n"), out_stream=out).loop()
    return out.getvalue()


def definition(app, word, module="Book"):
    return app.storage.load_words(module).words[word].primary_definition()


PATHS = [  # (typed, reaches EXPORT/IMPORT as)
    (r"C:\Users\Pat\book.json", r"C:\Users\Pat\book.json"),
    (r"\\server\share\book.json", r"\\server\share\book.json"),
    (r'"\\server\share\book.json"', r"\\server\share\book.json"),
    (r'"C:\Users\Pat Smith\book.json"', r"C:\Users\Pat Smith\book.json"),
    (r'"C:\Users\Pat\"', "C:\\Users\\Pat\\"),
    (r"C:\Users\O'Brien\book.json", r"C:\Users\O'Brien\book.json"),
    ("/home/pat/book.json", "/home/pat/book.json"),
]


def test_paths_reach_export_and_import_as_typed(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "cmd_export", lambda *args, **kw: calls.append(args[1]))
    monkeypatch.setattr(app, "cmd_import", lambda *args, **kw: calls.append(args[0]))
    for typed, expected in PATHS:
        calls.clear()
        run(app, f"EXPORT Book {typed}")
        run(app, f"IMPORT {typed} as Copy")
        assert calls == [expected, expected], typed


@pytest.mark.parametrize("prefix", [
    "",
    pytest.param("\\\\?\\", marks=pytest.mark.skipif(
        sys.platform != "win32", reason="the \\\\?\\ path prefix exists on Windows only")),
])
def test_unquoted_native_path_round_trips(app, tmp_path, prefix):
    # A backslash is a separator on Windows and part of the file name elsewhere;
    # either way the bundle must land at, and import from, exactly that path.
    app.cmd_add("ephemeral", manual_def="lasting a short time")
    folder = tmp_path / "out"
    folder.mkdir()
    path = prefix + str(folder) + "\\book.json"
    if " " in path:
        pytest.skip("the temporary folder's path contains spaces")
    assert "Exported" in run(app, f"EXPORT Book {path}")
    with open(path, encoding="utf-8") as bundle:
        assert json.load(bundle)["words"]
    run(app, f"IMPORT {path} as Copy")
    assert definition(app, "ephemeral", "Copy") == "lasting a short time"


def test_unclosed_quote_is_reported_and_writes_nothing(app, tmp_path, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    out = loop(app, 'EXPORT Book "C:\\Users\\Pat\\book.json', 'ADD "never :: x', "WHERE")
    assert out.count("! Unclosed quote") == 2
    assert "Current list: Book" in out  # the REPL carries on
    assert list(cwd.iterdir()) == []
    assert app.storage.load_words("Book").words == {}


def test_a_path_with_quote_characters_is_rejected(app, tmp_path, monkeypatch):
    # Single quotes do not group, so a path written in them must not become a
    # file whose name starts or ends with a quote.
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    for line in ("EXPORT Book 'book.json'", "EXPORT Book book.json\"",
                 "IMPORT 'book.json'"):
        assert "cannot start or end with a quote" in run(app, line), line
    assert set(tmp_path.iterdir()) == before


def test_stats_flag_anywhere_and_as_in_any_case(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app.cmd_add("ephemeral", manual_def="lasting a short time")
    assert "(with progress)" in run(app, "EXPORT Book --stats book.json")
    assert not (tmp_path / "--stats").exists()  # never a file name
    assert "stats" in json.loads((tmp_path / "book.json").read_text(encoding="utf-8"))
    assert "imported progress" in run(app, 'IMPORT --STATS book.json AS "My Copy"')
    assert "ephemeral" in app.storage.load_words("My Copy").words
    assert "fresh progress" in run(app, "IMPORT book.json as Second Copy")
    assert {"My Copy", "Second Copy"} <= set(app.storage.list_names())


def test_extra_words_and_missing_names_are_rejected(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "cmd_export", lambda *args, **kw: calls.append(args) or "ran")
    monkeypatch.setattr(app, "cmd_import", lambda *args, **kw: calls.append(args) or "ran")
    for line in (r"EXPORT Book C:\Users\Pat Smith\book.json",  # unquoted spaces
                 "EXPORT Book book.json extra",
                 "EXPORT book.json"):
        assert run(app, line).startswith("Usage: EXPORT"), line
    for line in (r"IMPORT C:\Users\Pat Smith\book.json",
                 r"IMPORT /tmp/My\ Files/book.json",  # no escaped spaces either
                 "IMPORT book.json as",
                 "IMPORT book.json to Copy",
                 "IMPORT --stats"):
        assert run(app, line).startswith("Usage: IMPORT"), line
    assert calls == []


def test_single_quotes_are_ordinary_characters(app):
    run(app, "CREATE Bob's Book")
    run(app, """ADDL "Bob's Book" don't :: do not""")
    run(app, """ADDL "Bob's Book" 'tis :: it is""")
    assert definition(app, "don't", "Bob's Book") == "do not"
    assert definition(app, "'tis", "Bob's Book") == "it is"
    run(app, "ADD 'n' :: and")  # a word in single quotes keeps them
    assert definition(app, "'n'") == "and"
    run(app, "CREATE \"Students' Notes\"")
    run(app, "CREATE 'Quoted'")
    assert {"Students' Notes", "'Quoted'"} <= set(app.storage.list_names())


def test_backslash_before_a_quote_stays_literal(app):
    # No escapes: \" is a backslash and a quote. A quoted word may contain
    # double quotes as long as the closing one ends the argument.
    run(app, r'ADD "say \"hi\"" :: an escaped greeting')
    assert definition(app, r'say \"hi\"') == "an escaped greeting"
    run(app, 'ADD "say "hi"" :: a quoted greeting')
    assert definition(app, 'say "hi"') == "a quoted greeting"


def test_separator_inside_quotes_and_definition_text_kept(app):
    run(app, r'ADD "std::vector" :: a "growable" array \ C++ :: type')
    assert definition(app, "std::vector") == r'a "growable" array \ C++ :: type'
    run(app, "ADD word::meaning")  # no spaces around '::' still works
    assert definition(app, "word") == "meaning"
    run(app, 'ADD quip :: the "so-called')  # the body is never split or unquoted
    assert definition(app, "quip") == 'the "so-called'


def test_separator_is_rejected_where_no_body_is_taken(app):
    app.cmd_add("ice cream", manual_def="frozen dessert")
    app.cmd_add("std::vector", manual_def="a growable array")
    reply = run(app, "DEL ice cream :: junk")
    assert "'::'" in reply and "ice cream" in app.storage.load_words("Book").words
    reply = run(app, "DEL std::vector")
    assert 'DEL "std::vector"' in reply  # says how to quote it
    assert "std::vector" in app.storage.load_words("Book").words
    run(app, 'DEL "std::vector"')
    assert "std::vector" not in app.storage.load_words("Book").words


# ---- hints that show a command ---------------------------------------------
PLAIN = ["ephemeral", "don't", r"C:\Users\Pat", 'say"hi', "hi\""]
NEEDS_QUOTES = ["", "ice cream", "tab\there", "std::vector", "::", '"quoted"', '"', 'say "hi"']


def test_quote_arg_reads_back_as_one_argument():
    from vocab.cli.parser import quote_arg, split_command
    for text in PLAIN + NEEDS_QUOTES:
        typed = quote_arg(text)
        assert typed == (f'"{text}"' if text in NEEDS_QUOTES else text)
        assert split_command(f"ADD {typed} :: a body") == (["ADD", text], "a body"), text


def test_quote_arg_refuses_text_that_no_form_reads_back():
    from vocab.cli.parser import CommandSyntaxError, quote_arg
    for text in ['say" hi', 'a"::b', 'a"\tb']:  # the quote would end the argument
        with pytest.raises(CommandSyntaxError):
            quote_arg(text)


def entries(app, module="Book"):
    return {key: (word.word, word.dictionary, word.primary_definition())
            for key, word in app.storage.load_words(module).words.items()}


TYPED_TERMS = [  # (typed after ADD, the term it adds)
    ('"std::vector"', "std::vector"),
    ('""quoted""', '"quoted"'),
    ('"say "hi""', 'say "hi"'),
    ("ice cream", "ice cream"),
]


def timed_out(word, sentence=""):
    from vocab.dictionaries import LookupFailed
    raise LookupFailed("The online dictionary timed out.", recoverable=True)


def add_manually_hint(app, line):
    """The command the reply to ``line`` suggests, up to its '::'."""
    with pytest.raises(ValueError) as failed:
        run(app, line)
    return re.search(r"\(add manually:  (.+) :: <your definition>\)$", str(failed.value)).group(1)


def test_add_manually_hint_typed_as_shown_adds_that_term(app, monkeypatch):
    for word in ("std", "quoted", "say", "ice"):  # what an unquoted hint would replace
        app.cmd_add(word, manual_def=f"my {word}")
    monkeypatch.setattr(app, "_lookup_senses", timed_out)
    for typed, term in TYPED_TERMS:
        before = entries(app)
        hinted = add_manually_hint(app, f"ADD {typed}")
        assert f"Added '{term}'" in run(app, f"{hinted} :: my own definition"), typed
        after = entries(app)
        assert after.pop(term.lower()) == (term, "manual", "my own definition")
        assert after == before  # no other entry changed


def test_add_manually_hint_after_addl_names_the_same_module(app, monkeypatch):
    run(app, 'CREATE "Moby Dick"')
    app.cmd_add("sperm whale", manual_def="mine")  # in Book, the current module
    before = entries(app)
    monkeypatch.setattr(app, "_lookup_senses", timed_out)
    hinted = add_manually_hint(app, 'ADDL "Moby Dick" sperm whale')
    assert hinted == 'ADDL "Moby Dick" "sperm whale"'
    assert "-> Moby Dick" in run(app, f"{hinted} :: a large toothed whale")
    assert entries(app, "Moby Dick") == {
        "sperm whale": ("sperm whale", "manual", "a large toothed whale")}
    assert entries(app) == before  # the current module is untouched


def test_replace_hint_typed_after_add_replaces_that_term(app, monkeypatch):
    from vocab.core.models import Sense
    for _typed, term in TYPED_TERMS:
        app.cmd_add(term, manual_def="mine")
    for word in ("std", "quoted", "say", "ice"):
        app.cmd_add(word, manual_def=f"my {word}")
    monkeypatch.setattr(app, "_lookup_senses", lambda word, sentence="": (
        [Sense("a looked-up sense")], "wordnet", ""))
    for typed, term in TYPED_TERMS:
        before = entries(app)
        reply = run(app, f"ADD {typed}")
        assert "kept the existing entry" in reply and entries(app) == before
        hinted = re.search(r"own definition \((.+) :: \.\.\.\)\.$", reply)
        assert f"Updated '{term}'" in run(app, f"ADD {hinted.group(1)} :: replaced"), typed
        after = entries(app)
        assert after.pop(term.lower()) == (term, "manual", "replaced")
        before.pop(term.lower())
        assert after == before  # no other entry changed


# ---- multi-word terms ------------------------------------------------------
def test_multiword_terms_round_trip_in_current_module(app):
    assert "Added 'ice cream'" in run(app, "ADD ice cream :: frozen dessert")
    assert definition(app, "ice cream") == "frozen dessert"
    assert "Removed 'ice cream'" in run(app, "DEL ice cream")
    run(app, "ADD ephemeral :: brief")  # one-word terms are unchanged
    assert sorted(app.storage.load_words("Book").words) == ["ephemeral"]


def test_multiword_terms_round_trip_in_a_quoted_module(app):
    run(app, 'CREATE "Moby Dick"')
    run(app, 'ADDL "Moby Dick" sperm whale :: a large toothed whale')
    assert definition(app, "sperm whale", "Moby Dick") == "a large toothed whale"
    run(app, "ADDL Book hot dog :: sausage")
    assert definition(app, "hot dog") == "sausage"
    assert "Removed 'sperm whale'" in run(app, 'DELL "Moby Dick" sperm whale')
    assert "Removed 'hot dog'" in run(app, "DELL Book hot dog")
    assert app.storage.load_words("Moby Dick").words == {}
    assert app.storage.load_words("Book").words == {}


def test_multiword_lookup_uses_the_whole_term(app, monkeypatch):
    from vocab.core.models import Sense
    looked_up = []
    monkeypatch.setattr(app, "_lookup_senses", lambda word, sentence="": (
        looked_up.append(word) or ([Sense("a looked-up sense")], "wordnet", "")))
    run(app, "ADD ice cream")
    run(app, 'ADDL Book "hot dog"')
    run(app, "ADD rock 'n' roll")
    assert looked_up == ["ice cream", "hot dog", "rock 'n' roll"]
    assert sorted(app.storage.load_words("Book").words) == ["hot dog", "ice cream", "rock 'n' roll"]


def test_multiword_terms_with_real_wordnet(app):
    from vocab.dictionaries.wordnet import WordNet
    if not WordNet().available():
        pytest.skip("the WordNet corpus is not installed (set NLTK_DATA)")
    assert "frozen dessert" in run(app, "ADD ice cream")
    run(app, "ADDL Book hot dog")
    run(app, "ADD rock 'n' roll")
    words = app.storage.load_words("Book").words
    assert sorted(words) == ["hot dog", "ice cream", "rock 'n' roll"]
    assert {word.dictionary for word in words.values()} == {"wordnet"}
    assert definition(app, "ice cream").startswith("frozen dessert containing cream")
    assert "popular music" in definition(app, "rock 'n' roll")
    run(app, "DEL ice cream")
    run(app, "DELL Book hot dog")
    run(app, "DEL rock 'n' roll")
    assert app.storage.load_words("Book").words == {}


# ---- NOTE ADD --------------------------------------------------------------
def test_bodyless_note_add_keeps_an_existing_note(app):
    app.storage.save_note("Book", "Chapter 1", "hours of notes\n")
    path = app.storage._note_path("Book", "Chapter 1")
    before = path.read_bytes()
    for line in ("NOTE ADD Chapter 1", 'NOTE ADD "Chapter 1"'):
        reply = run(app, line)
        assert "'Chapter 1' already exists" in reply and "left unchanged" in reply
        assert "::" not in reply  # never suggests the form that replaces the text
    assert path.read_bytes() == before


def test_bodyless_note_add_creates_an_empty_note(app):
    assert "Created note 'My Note'" in run(app, 'NOTE ADD "My Note"')
    assert "Created note 'Bob's plan'" in run(app, "NOTE ADD Bob's plan")
    assert app.storage.load_note("Book", "My Note") == ""
    assert app.storage.load_note("Book", "Bob's plan") == ""


def test_note_add_with_text_creates_or_replaces(app):
    assert "Created note 'My Note'" in run(app, r'NOTE ADD "My Note" :: # Plan "A" \ draft')
    assert app.storage.load_note("Book", "My Note") == r'# Plan "A" \ draft'
    assert "Replaced note 'My Note'" in run(app, 'NOTE ADD "My Note" :: second')
    assert app.storage.load_note("Book", "My Note") == "second"
    # An explicit empty body is a deliberate replace.
    assert "Replaced note 'My Note'" in run(app, 'NOTE ADD "My Note" ::')
    assert app.storage.load_note("Book", "My Note") == ""


def test_bodyless_note_add_keeps_a_windows_case_alias_and_a_legacy_name(app, monkeypatch):
    storage = app.storage
    storage.save_note("Book", "One", "original")
    notes_dir = storage.paths.notes_dir("Book")
    (notes_dir / "Double.md.md").write_text("legacy text", encoding="utf-8")
    original = storage._note_path

    def insensitive(*args, **kwargs):  # Windows: existing names match in any case
        path = original(*args, **kwargs)
        return next((p for p in path.parent.iterdir()
                     if p.name.casefold() == path.name.casefold()), path)
    monkeypatch.setattr(storage, "_note_path", insensitive)
    assert "'One' already exists" in run(app, "NOTE ADD one")
    assert "'Double.md' already exists" in run(app, "NOTE ADD Double.md")
    assert (notes_dir / "One.md").read_text(encoding="utf-8") == "original"
    assert (notes_dir / "Double.md.md").read_text(encoding="utf-8") == "legacy text"
