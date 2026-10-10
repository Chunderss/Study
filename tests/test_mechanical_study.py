"""Typed-answer (mechanical) study through the session, run_study and the REPL."""
import io

import pytest

from vocab.cli.app import App
from vocab.cli.repl import Repl
from vocab.study.judge import KeywordJudge
from vocab.study.session import StudySession

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


def test_terminal_feedback_states_the_verdict_the_session_records(app):
    add_due(app, "tally", "large amount")
    answers = iter(["large"])
    output = []
    summary = app.run_study(["Book"], lambda _text: next(answers), output.append)
    assert ("  score 50% — Accepted — you recalled enough key terms. "
            "Key terms not mentioned: amount.") in output
    assert "1/1 correct" in summary
    assert app.storage.load_stats("Book")["tally"]["box"] == 5


def test_session_records_the_judges_verdict(app):
    class StrictJudge(KeywordJudge):
        def score(self, user_answer, reference):
            result = super().score(user_answer, reference)
            result.passed = False
            return result

    add_due(app, "alpha", "first letter")
    session = StudySession(app.storage, app.scheduler, ["Book"],
                           mechanical=True, judge=StrictJudge())
    outcome = session.answer_text("first letter")
    assert outcome.score == 1.0 and not outcome.correct
    assert app.storage.load_stats("Book")["alpha"]["box"] == 1
