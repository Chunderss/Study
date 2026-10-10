r"""One command grammar: Windows paths, double quotes, '::' bodies.

Dispatch-level: each line goes through parse() and Repl.dispatch() (or the
whole REPL loop), exactly as typed in the terminal or the desktop Console.
Backslashes and single quotes are ordinary characters; only a double quote
at the start of an argument groups it.
"""
import io
import json
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
