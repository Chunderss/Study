"""HELP, the banner and the package agree with what the terminal does."""
import io
from pathlib import Path
import re
import sys

import pytest

from vocab.cli.app import App
from vocab.cli.help_text import BANNER, HELP
from vocab.cli.parser import parse
from vocab.cli.repl import Repl

ROOT = Path(__file__).resolve().parents[1]


def _repl(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Gatsby")
    app.cmd_use("Gatsby")
    return app, Repl(app, io.StringIO(""), io.StringIO())


def test_list_alias_lists_modules(tmp_path):
    app, repl = _repl(tmp_path)
    expected = app.cmd_lists()
    assert [repl.dispatch(parse(verb)) for verb in ("LIST", "lists", "MODULES")] == [expected] * 3


def test_every_verb_in_help_is_dispatched(tmp_path):
    app, repl = _repl(tmp_path)
    verbs = set(re.findall(r"^  ([A-Z]{2,})\b", HELP, re.M))
    verbs |= set(re.findall(r"alias: ([A-Z]{2,})", HELP))
    verbs |= set(re.search(r"\(([A-Z/]+) still work as aliases", HELP).group(1).split("/"))
    verbs |= {"EXIT"}
    assert {"LIST", "CD", "PWD", "QUIT", "MODULES"} <= verbs
    for verb in sorted(verbs):
        if verb in ("QUIT", "EXIT"):
            with pytest.raises(EOFError):
                repl.dispatch(parse(verb))
            continue
        assert not (repl.dispatch(parse(verb)) or "").startswith("Unknown command"), verb


def test_displayed_versions_come_from_the_project_version(monkeypatch, capsys):
    from vocab.__main__ import main
    from vocab.dictionaries import freedict
    from vocab.version import __version__
    assert f"Vocab Study  v{__version__}" in BANNER
    assert freedict._UA.startswith(f"VocabStudy/{__version__} ")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # Text match: tomllib is not available on the Python 3.10 floor.
    assert re.search(r'^dynamic = \["version"\]', pyproject, re.M)
    assert not re.search(r'^version = "', pyproject, re.M)  # no second, static copy
    assert 'version = {attr = "vocab.version.__version__"}' in pyproject
    monkeypatch.setattr(sys, "argv", ["vocab", "--version"])
    with pytest.raises(SystemExit) as done:
        main()
    assert done.value.code == 0
    assert capsys.readouterr().out.strip() == f"Vocab Study {__version__}"
