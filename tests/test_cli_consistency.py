"""HELP, the banner and the package agree with what the terminal does."""
import io
import re

import pytest

from vocab.cli.app import App
from vocab.cli.help_text import HELP
from vocab.cli.parser import parse
from vocab.cli.repl import Repl


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
