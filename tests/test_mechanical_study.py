"""Typed-answer (mechanical) study through the session, run_study and the REPL."""
import io

import pytest

from vocab.cli.app import App
from vocab.cli.repl import Repl

PROMPT = "  your definition (or 'q'): "


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.config.mechanical_repetition = True
    return app


def add_due(app, word, definition, box=4, reps=7):
    app.cmd_add(word, manual_def=definition)
    stats = app.storage.load_stats("Book")
    stats.setdefault(word, app.scheduler.new_card()).update(box=box, due=0.0, reps=reps)
    app.storage.save_stats("Book", stats)
    return stats[word]


def test_terminal_study_reprompts_on_a_blank_answer(app):
    before = add_due(app, "alpha", "first letter", box=5)
    answers = iter(["", "   ", "\r", "q"])
    prompts, output = [], []

    def prompt(text):
        prompts.append(text)
        return next(answers)

    summary = app.run_study(["Book"], prompt, output.append)
    assert prompts == [PROMPT] * 4
    assert not any("score" in line for line in output)
    assert "0/0 correct" in summary
    assert app.storage.load_stats("Book")["alpha"] == before


def test_repl_blank_lines_during_study_do_not_grade(app):
    before = add_due(app, "alpha", "first letter", box=5)
    out = io.StringIO()
    # LF, CRLF and whitespace-only blank lines all go through Repl.prompt.
    Repl(app, in_stream=io.StringIO("STUDY\n\n\r\n   \nfirst letter\nQUIT\n"),
         out_stream=out).loop()
    after = app.storage.load_stats("Book")["alpha"]
    assert out.getvalue().count("your definition") == 4
    assert "Unknown command" not in out.getvalue()
    assert after["lapses"] == 0 and after["reps"] == before["reps"] + 1
    assert after["box"] == 5
