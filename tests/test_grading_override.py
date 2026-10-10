"""Mechanical grading is reviewable: the judge suggests a verdict, the user
keeps or overrules it, and only then is progress recorded."""
import io

import pytest

from vocab.cli.app import App
from vocab.cli.repl import Repl
from vocab.study.judge import KeywordJudge
from vocab.study.session import StudyCardUnavailable, StudySession

DEFINITION = "  your definition (or 'q'): "


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Repl, "_startup_checks", lambda self: None)
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


def mechanical_session(app):
    return StudySession(app.storage, app.scheduler, ["Book"], mechanical=True,
                        judge=KeywordJudge())


def test_the_judge_suggests_and_the_users_verdict_is_recorded(app):
    # A documented judge limit: a reversal that keeps a key term passes at 50%.
    before = add_due(app, "broke", "having no money")
    session = mechanical_session(app)
    judged = session.judge_text("  having money ")
    assert (judged.suggested, judged.score, judged.typed) == (True, 0.5, "having money")
    assert judged.reference == "having no money" and judged.feedback.startswith("Accepted")
    assert session.judgement is judged
    assert app.storage.load_stats("Book")["broke"] == before    # nothing recorded yet
    assert session.reviewed == 0

    outcome = session.answer(False)
    assert not outcome.correct and outcome.suggested is True and outcome.overruled
    assert (outcome.box_before, outcome.box_after, outcome.score) == (4, 1, 0.5)
    card = app.storage.load_stats("Book")["broke"]
    assert (card["box"], card["lapses"], card["reps"]) == (1, 1, 8)
    assert session.judgement is None and session.done
    assert session.finish() == ("Session done: 0/1 correct (0%). "
                                "You overruled the judge on 1 card(s).")


def test_keeping_the_judges_verdict_is_not_an_overrule(app):
    add_due(app, "tally", "large amount")
    add_due(app, "existent", "existing")
    session = mechanical_session(app)
    answers = {"tally": ("large", True), "existent": ("not existing", False)}
    for _ in answers:
        typed, verdict = answers[session.current().word]
        assert session.judge_text(typed).suggested is verdict
        outcome = session.answer(verdict)
        assert not outcome.overruled and outcome.box_after == (5 if verdict else 1)
    assert session.finish() == "Session done: 1/2 correct (50%)."


def test_a_card_the_judge_cannot_grade_is_graded_by_the_user(app):
    add_due(app, "dash", "—", box=2)
    session = mechanical_session(app)
    judged = session.judge_text("a dash")
    assert judged.suggested is None and judged.score is None
    assert "cannot grade" in judged.feedback
    outcome = session.answer(True)
    assert outcome.correct and not outcome.overruled and outcome.box_after == 3
    assert app.storage.load_stats("Book")["dash"]["box"] == 3
    assert session.finish() == "Session done: 1/1 correct (100%)."


def test_a_mechanical_verdict_needs_a_judged_answer(app):
    before = add_due(app, "alpha", "first letter")
    session = mechanical_session(app)
    with pytest.raises(RuntimeError, match="Type a definition"):
        session.answer(True)
    with pytest.raises(ValueError, match="Type a definition"):
        session.judge_text("  ")
    assert app.storage.load_stats("Book")["alpha"] == before
    assert session.reviewed == 0 and session.current().word == "alpha"


def test_a_failed_save_keeps_the_judged_answer_for_a_retry(app, monkeypatch):
    add_due(app, "alpha", "first letter")
    session = mechanical_session(app)
    judged = session.judge_text("first letter")
    save = app.storage.save_stats

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(app.storage, "save_stats", fail)
    with pytest.raises(OSError):
        session.answer(False)
    assert session.judgement is judged and session.reviewed == 0
    monkeypatch.setattr(app.storage, "save_stats", save)
    assert session.answer(False).overruled
    assert app.storage.load_stats("Book")["alpha"]["lapses"] == 1


def test_a_card_changed_while_judged_is_skipped_unrecorded(app):
    add_due(app, "alpha", "first letter")
    add_due(app, "beta", "second letter")
    session = mechanical_session(app)
    session.judge_text("first letter")
    app.cmd_add("alpha", manual_def="the first letter")
    saved = app.storage.load_stats("Book")
    with pytest.raises(StudyCardUnavailable, match="changed in another session"):
        session.answer(True)
    assert app.storage.load_stats("Book") == saved
    assert session.judgement is None and session.current().word == "beta"
    with pytest.raises(RuntimeError):
        session.answer(True)                 # beta's answer was not judged


def scripted(replies):
    """A prompt that answers in order and records each question."""
    asked = []
    replies = iter(replies)

    def prompt(text):
        asked.append(text)
        return next(replies)

    return prompt, asked


def test_terminal_enter_keeps_the_judges_verdict_and_y_or_n_overrule_it(app):
    add_due(app, "broke", "having no money")
    add_due(app, "existent", "existing")
    add_due(app, "tally", "large amount")
    output = []
    prompt, asked = scripted(["having money", "n", "not existing", "Y",
                              "large", ""])
    summary = app.run_study(["Book"], prompt, output.append)
    assert asked == [DEFINITION, "  did you get it? [Y/n] (or 'q'): ",
                     DEFINITION, "  did you get it? [y/N] (or 'q'): ",
                     DEFINITION, "  did you get it? [Y/n] (or 'q'): "]
    assert "  You overruled the judge: recorded as missed." in output
    assert "  You overruled the judge: recorded as correct." in output
    assert output.count("  reference: existing") == 1
    assert summary.endswith("Session done: 2/3 correct (67%). "
                            "You overruled the judge on 2 card(s).")
    stats = app.storage.load_stats("Book")
    assert [stats[w]["box"] for w in ("broke", "existent", "tally")] == [1, 5, 5]


def test_terminal_verdict_needs_a_clear_reply_and_q_stops_unrecorded(app):
    dash = add_due(app, "dash", "—")
    alpha = add_due(app, "alpha", "first letter", box=1)
    output = []
    # alpha (box 1) comes first; then dash, which the judge cannot grade.
    prompt, asked = scripted(["first letter", "maybe", "  YES ",
                              "a dash", "", "q"])
    summary = app.run_study(["Book"], prompt, output.append)
    verdicts = [q for q in asked if q != DEFINITION]
    assert verdicts == ["  did you get it? [Y/n] (or 'q'): "] * 2 + \
        ["  did you get it? [y/n] (or 'q'): "] * 2
    assert any("cannot grade" in line for line in output)
    assert summary.endswith("Session done: 1/1 correct (100%).")
    stats = app.storage.load_stats("Book")
    assert stats["dash"] == dash and stats["alpha"]["box"] == alpha["box"] + 1


def test_terminal_failed_save_asks_for_the_verdict_again(app, monkeypatch):
    add_due(app, "alpha", "first letter", box=1)
    add_due(app, "beta", "second letter")
    save = app.storage.save_stats
    failures = [OSError("disk full")]

    def save_once_failing(*args):
        if failures:
            raise failures.pop()
        save(*args)

    monkeypatch.setattr(app.storage, "save_stats", save_once_failing)
    output = []
    prompt, asked = scripted(["first letter", "", "", "second letter", "n"])
    summary = app.run_study(["Book"], prompt, output.append)
    assert "  Could not save review: disk full" in output
    # The judged answer survives the failure: the verdict is asked again.
    assert asked == [DEFINITION, "  did you get it? [Y/n] (or 'q'): ",
                     "  did you get it? [Y/n] (or 'q'): ",
                     DEFINITION, "  did you get it? [Y/n] (or 'q'): "]
    assert summary.endswith("Session done: 1/2 correct (50%). "
                            "You overruled the judge on 1 card(s).")
    stats = app.storage.load_stats("Book")
    assert (stats["alpha"]["box"], stats["beta"]["box"]) == (2, 1)


def test_repl_failed_save_keeps_the_session_and_its_answers(app, monkeypatch):
    add_due(app, "alpha", "first letter")
    save = app.storage.save_stats
    failures = [OSError("disk full")]

    def save_once_failing(*args):
        if failures:
            raise failures.pop()
        save(*args)

    monkeypatch.setattr(app.storage, "save_stats", save_once_failing)
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO("STUDY\nfirst letter\ny\ny\nQUIT\n"),
         out_stream=out).loop()
    assert "Could not save review: disk full" in out.getvalue()
    assert "Unknown command" not in out.getvalue()
    assert app.storage.load_stats("Book")["alpha"]["box"] == 5


def test_repl_study_records_the_typed_verdict(app):
    add_due(app, "broke", "having no money")
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO("STUDY\nhaving money\nno\nQUIT\n"), out_stream=out).loop()
    card = app.storage.load_stats("Book")["broke"]
    assert (card["box"], card["lapses"]) == (1, 1)
    assert "Unknown command" not in out.getvalue()


def test_repl_end_of_input_at_the_verdict_records_nothing(app):
    before = add_due(app, "alpha", "first letter")
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO("STUDY\nfirst letter\n"), out_stream=out).loop()
    assert "did you get it?" in out.getvalue() and "Goodbye" in out.getvalue()
    assert app.storage.load_stats("Book")["alpha"] == before
