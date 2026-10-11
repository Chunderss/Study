"""Typed-answer (mechanical) study in the Qt study view."""
import pytest

pytest.importorskip("PySide6")

from .test_gui_reliability import qt, window
from vocab.gui.study_view import StudyView
from vocab.study.judge import KeywordJudge
from vocab.study.session import StudySession

VEGAN = "a strict vegetarian; someone who eats no animal or dairy products at all"


def mechanical_view(window, qt):
    session = StudySession(window.app.storage, window.app.scheduler, ["Book"],
                           mechanical=True, judge=KeywordJudge())
    view = StudyView(session)
    view.resize(500, 400)
    view.show()
    qt.processEvents()
    return session, view


def test_long_feedback_wraps_instead_of_widening_the_study_view(window, qt, monkeypatch):
    window.app.cmd_add("vegan", manual_def=VEGAN)
    session, view = mechanical_view(window, qt)
    view.input.setText("a vegetarian who avoids animal and dairy products")
    # The reference and the longer "J / Enter" button also change the layout.
    # Settle those first: their minimum width depends on the native font/style,
    # so 500 px is not always a valid width even without any judge feedback.
    messages = []
    with monkeypatch.context() as patch:
        patch.setattr(view.feedback, "setText", messages.append)
        view._submit_typed()
    qt.processEvents()
    before = view.width()
    view.feedback.setText(messages[-1])
    qt.processEvents()
    feedback = view.feedback.text()
    assert "Key terms not mentioned" in feedback and len(feedback) > 90
    assert view.minimumSizeHint().width() <= view.width() == before
    # The judge states the verdict; the view does not repeat it.
    assert feedback.count("Accepted") == 1 and "(correct)" not in feedback
    view.deleteLater()
    qt.processEvents()
