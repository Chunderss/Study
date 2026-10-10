"""Reviewable mechanical grading in the study view, driven by real key events.

After Enter in the answer box the judge's verdict is only a suggestion: the
view takes focus, Enter keeps the suggestion, J/Y and F/N choose, a second
Space or a modified chord records nothing, and Tab or the mouse reach the
buttons. Nothing is recorded until the verdict.
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from .test_gui_reliability import qt, window
from .test_study_keyboard_gui import DEFINITIONS, DELIVERY, Keys, _open_study

WRONG = "nothing at all"
CHORDS = [(modifiers, key)
          for modifiers in (Qt.ControlModifier, Qt.AltModifier, Qt.MetaModifier)
          for key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)]


def stats(window):
    return window.app.storage.load_stats("Book")


def submit(keys, view, answer=None):
    word = view.word.text()
    view.input.setText(dict(DEFINITIONS, epsilon="fifth letter")[word]
                       if answer is None else answer)
    keys.press(Qt.Key_Return)
    return word


def hold_typing(keys, key, text):
    """A held letter as a keyboard sends it: auto-repeats carry the text."""
    target = keys.target()
    keys.qt.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text))
    for _ in range(3):
        target = keys.target()
        keys.qt.sendEvent(target, QKeyEvent(QEvent.KeyRelease, key, Qt.NoModifier, text, True))
        keys.qt.sendEvent(target, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, text, True))
    keys.qt.sendEvent(keys.target(), QKeyEvent(QEvent.KeyRelease, key, Qt.NoModifier, text))
    keys.qt.processEvents()


@DELIVERY
def test_enter_keeps_the_judges_verdict_and_j_or_f_overrule_it(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    view = _open_study(window, qt, mechanical=True)
    summaries = []
    view.finished.connect(summaries.append)
    session = view.session

    first = submit(keys, view)                      # right: the judge suggests Got it
    assert session.judgement.suggested is True and session.reviewed == 0
    assert all(card["reps"] == 0 for card in stats(window).values())
    assert qt.focusWidget() is view and not view.input.isEnabled()
    assert view.input.text() == DEFINITIONS[first]
    assert view.answer.text() == "reference:  " + DEFINITIONS[first]
    assert view.feedback.text().startswith("score 100% — Accepted")
    assert view.good_btn.isEnabled() and view.bad_btn.isEnabled()
    assert view.good_btn.text() == "Got it  (J / Enter)" and view.bad_btn.text() == "Missed it  (F)"
    keys.press(Qt.Key_Return)                       # keeps it
    assert (session.reviewed, session.correct) == (1, 1)
    assert qt.focusWidget() is view.input and view.input.text() == ""
    assert view.feedback.text() == "box 1 ↑ 2"
    assert view.good_btn.text() == "Got it  (J)" and not view.good_btn.isEnabled()

    second = submit(keys, view, WRONG)             # wrong: the judge suggests Missed it
    assert session.judgement.suggested is False
    assert view.bad_btn.text() == "Missed it  (F / Enter)"
    keys.press(Qt.Key_J)                            # overruled: Got it
    assert (session.reviewed, session.correct) == (2, 2)
    assert view.feedback.text() == "box 1 ↑ 2   ·   you overruled the judge"

    third = submit(keys, view)
    keys.press(Qt.Key_F)                            # overruled: Missed it
    assert (session.reviewed, session.correct) == (3, 2)
    fourth = submit(keys, view, WRONG)
    keys.press(Qt.Key_Enter)                        # keeps Missed it
    assert (session.reviewed, session.correct) == (4, 2) and view._ended
    saved = stats(window)
    assert [(saved[w]["box"], saved[w]["lapses"]) for w in (first, second, third, fourth)] == \
        [(2, 0), (2, 0), (1, 1), (1, 1)]
    assert summaries == ["Session done: 2/4 correct (50%). You overruled the judge on 2 card(s)."]


@DELIVERY
def test_space_held_keys_and_chords_never_record_a_judged_answer(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    view = _open_study(window, qt, mechanical=True)
    session = view.session
    view.input.setText(DEFINITIONS[view.word.text()])
    keys.hold(Qt.Key_Return)            # judges once; the repeats reach the card
    assert session.judgement is not None and session.reviewed == 0
    keys.press(Qt.Key_Space)
    keys.press(Qt.Key_Space)
    for modifiers, key in [(Qt.ControlModifier, Qt.Key_J), (Qt.ControlModifier, Qt.Key_F),
                           (Qt.AltModifier, Qt.Key_Y), (Qt.MetaModifier, Qt.Key_N),
                           (Qt.ControlModifier, Qt.Key_Return), (Qt.AltModifier, Qt.Key_Enter)]:
        keys.press(key, modifiers)
    assert session.reviewed == 0 and session.judgement is not None
    assert all(card["reps"] == 0 for card in stats(window).values())

    keys.press(Qt.Key_Y, Qt.ShiftModifier)          # Shift still counts
    assert (session.reviewed, session.correct) == (1, 1)

    submit(keys, view, WRONG)
    hold_typing(keys, Qt.Key_J, "j")                # records once
    assert (session.reviewed, session.correct) == (2, 2)
    assert view.input.text() == "" and session.judgement is None   # no "jjj" typed

    submit(keys, view)
    keys.press(Qt.Key_Enter, Qt.KeypadModifier)     # the keypad Enter keeps it too
    assert (session.reviewed, session.correct) == (3, 3)


@DELIVERY
def test_tab_and_mouse_reach_the_verdict_buttons(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    window.app.cmd_add("epsilon", manual_def="fifth letter")
    view = _open_study(window, qt, mechanical=True)
    session = view.session
    assert not view.good_btn.isEnabled() and not view.bad_btn.isEnabled()
    keys.click(view.good_btn)                       # nothing judged yet
    assert session.reviewed == 0 and qt.focusWidget() is view.input

    submit(keys, view)
    for button in (view.good_btn, view.bad_btn, view.end_btn):
        keys.tab_to(button)
        for modifiers, key in CHORDS:               # shortcuts, never a click
            keys.press(key, modifiers)
    assert session.reviewed == 0 and session.judgement is not None and not view._ended
    assert all(card["reps"] == 0 for card in stats(window).values())
    keys.tab_to(view.bad_btn)
    keys.press(Qt.Key_Return)                       # Enter on the focused button
    assert (session.reviewed, session.correct) == (1, 0)
    assert qt.focusWidget() is view.input

    submit(keys, view)
    keys.tab_to(view.good_btn)
    keys.press(Qt.Key_Space)                        # Space on a Tab-reached button
    assert (session.reviewed, session.correct) == (2, 1)

    submit(keys, view, WRONG)                       # the judge suggests Missed it
    keys.tab_to(view.good_btn)
    keys.press(Qt.Key_Enter, Qt.KeypadModifier)     # the focused button wins
    assert (session.reviewed, session.correct) == (3, 2)

    submit(keys, view, WRONG)
    keys.click(view.good_btn)
    assert (session.reviewed, session.correct) == (4, 3)
    assert qt.focusWidget() is view.input

    last = submit(keys, view)
    keys.tab_to(view.end_btn)
    keys.press(Qt.Key_Return)                       # ends; the judged card stays unrecorded
    assert view._ended and session.reviewed == 4
    assert stats(window)[last]["reps"] == 0


def leave_the_pane(keys, view, modifiers=Qt.NoModifier):
    """Tab (or Shift+Tab) until the focus leaves the study pane."""
    for _ in range(40):
        keys.press(Qt.Key_Tab, modifiers)
        widget = keys.qt.focusWidget()
        if widget is not None and widget is not view and not view.isAncestorOf(widget):
            return widget
    raise AssertionError(f"the keyboard focus stayed in the study pane on {widget!r}")


@DELIVERY
def test_tab_and_shift_tab_leave_the_answer_box_and_a_click_brings_it_back(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    view = _open_study(window, qt, mechanical=True)
    leave_the_pane(keys, view, Qt.ShiftModifier)    # no keyboard trap backwards
    keys.click(view.word)                           # a click on the card
    assert qt.focusWidget() is view.input
    leave_the_pane(keys, view)
    keys.click(view.good_btn)                       # or on a disabled verdict button
    assert qt.focusWidget() is view.input
    keys.press(Qt.Key_Tab, Qt.ShiftModifier)        # stops on the card on its way out
    assert qt.focusWidget() is view
    keys.press(Qt.Key_Tab)
    assert qt.focusWidget() is view.input           # and Tab goes back to the answer box
    view.input.setText("first letter")
    keys.press(Qt.Key_Return)
    assert view.session.judgement is not None and view.session.reviewed == 0


@DELIVERY
def test_a_card_the_judge_cannot_grade_is_graded_in_the_study_view(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    window.app.cmd_add("ampersand", manual_def="&")
    window.app.cmd_add("zeta", manual_def="last letter")
    window.app.config.mechanical_repetition = True
    window._open_study(["Book"])
    window.activateWindow()
    qt.processEvents()
    view = window.workspace.focused.component
    assert view.word.text() == "ampersand"
    view.input.setText("the and sign")
    keys.press(Qt.Key_Return)
    assert view.session.judgement.suggested is None
    assert "cannot grade" in view.feedback.text()
    assert view.answer.text() == "reference:  &"
    assert "Enter" not in view.good_btn.text() + view.bad_btn.text()
    keys.press(Qt.Key_Return)                       # no suggestion to keep
    keys.press(Qt.Key_Enter)
    assert view.session.reviewed == 0 and view.word.text() == "ampersand"
    keys.press(Qt.Key_J)
    assert view.session.reviewed == 1 and view.word.text() == "zeta"
    assert stats(window)["ampersand"]["box"] == 2


def test_a_failed_save_keeps_the_judged_answer_for_a_retry(window, qt):
    keys = Keys(window, qt, "native window")
    view = _open_study(window, qt, mechanical=True)
    word = submit(keys, view)

    def fail(*args):
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(window.app.storage, "save_stats", fail)
        for _ in range(2):              # a retry does not repeat the notice
            keys.press(Qt.Key_J)
    assert view.feedback.text() == "Could not save review: disk full"
    description = view.accessibleDescription()
    assert description.startswith("Could not save review: disk full. " + word)
    assert description.count("disk full") == 1
    assert view.session.judgement is not None and view.session.reviewed == 0
    assert view.input.text() == DEFINITIONS[word] and qt.focusWidget() is view
    keys.press(Qt.Key_Return)
    assert view.session.reviewed == 1 and stats(window)[word]["box"] == 2


def test_the_focused_widget_describes_the_mechanical_card(window, qt):
    keys = Keys(window, qt, "native window")
    view = _open_study(window, qt, mechanical=True)
    word = view.word.text()
    assert view.input.accessibleDescription() == (
        f"Card 1 of 4: {word}. Type the definition, then press Enter.")
    submit(keys, view)
    description = view.accessibleDescription()
    assert description.startswith(f"{word}. You wrote: {DEFINITIONS[word]}. "
                                  f"Reference: {DEFINITIONS[word]}. score 100% — Accepted")
    assert description.endswith("Press Enter to keep the judge's verdict, Got it; "
                                "J if you got it, or F if you missed it.")
    keys.press(Qt.Key_F)
    assert view.input.accessibleDescription().startswith(
        "Missed it: box 1 to 1. You overruled the judge. Card 2 of 4: ")
