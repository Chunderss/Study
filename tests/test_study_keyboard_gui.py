"""Study keys are real key events, sent to the focused widget and through the
native window as platform keyboard input arrives.

A second Space must never turn into "Got it": the keys a user presses go to
the card, while the buttons stay reachable with Tab and the mouse.
"""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from .test_gui_reliability import qt, window
from vocab.gui.study_view import StudyView

DEFINITIONS = {"alpha": "first letter", "beta": "second letter",
               "gamma": "third letter", "delta": "fourth letter"}


def _open_study(window, qt):
    for word, definition in DEFINITIONS.items():
        window.app.cmd_add(word, manual_def=definition)
    window._open_study(["Book"])        # the Study button's path
    window.activateWindow()
    qt.processEvents()
    view = window.workspace.focused.component
    assert isinstance(view, StudyView)
    assert qt.focusWidget() is view
    return view


class Keys:
    """Press keys at the focused widget, or at the window's native handle."""

    def __init__(self, window, qt, delivery):
        self.window, self.qt, self.native = window, qt, delivery == "native window"

    def target(self):
        return self.window.windowHandle() if self.native else self.qt.focusWidget()

    def press(self, key):
        QTest.keyClick(self.target(), key)
        self.qt.processEvents()

    def hold(self, key, repeats=3):
        """One press, auto-repeated release/press pairs, then one release."""
        QTest.keyPress(self.target(), key)
        for _ in range(repeats):
            for kind in (QEvent.KeyRelease, QEvent.KeyPress):
                self.qt.sendEvent(self.target(), QKeyEvent(kind, key, Qt.NoModifier, "", True))
        QTest.keyRelease(self.target(), key)
        self.qt.processEvents()

    def tab_to(self, widget):
        for _ in range(30):
            if self.qt.focusWidget() is widget:
                return
            self.press(Qt.Key_Tab)
        raise AssertionError(f"Tab never reached {widget.text()!r}")

    def click(self, button):
        if self.native:
            point = button.mapTo(self.window, button.rect().center())
            QTest.mouseClick(self.window.windowHandle(), Qt.LeftButton, Qt.NoModifier, point)
        else:
            QTest.mouseClick(button, Qt.LeftButton)
        self.qt.processEvents()


DELIVERY = pytest.mark.parametrize("delivery", ["focused widget", "native window"])


@DELIVERY
def test_space_and_enter_never_grade_a_revealed_card(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    view = _open_study(window, qt)
    graded = [view.word.text()]
    keys.press(Qt.Key_Space)
    assert view._revealed and view.session.reviewed == 0
    for key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
        keys.press(key)
    assert view.session.reviewed == 0 and view.word.text() == graded[0]
    assert all(card["reps"] == 0 for card in window.app.storage.load_stats("Book").values())

    keys.press(Qt.Key_J)
    assert (view.session.reviewed, view.session.result.correct) == (1, 1)
    assert not view._revealed and qt.focusWidget() is view
    graded.append(view.word.text())
    keys.press(Qt.Key_Return)
    assert view._revealed
    keys.press(Qt.Key_Return)
    keys.press(Qt.Key_Space)
    assert view.session.reviewed == 1
    keys.press(Qt.Key_F)
    assert (view.session.reviewed, view.session.result.correct) == (2, 1)

    graded.append(view.word.text())
    keys.hold(Qt.Key_Space)             # reveals once, grades nothing
    assert view._revealed and view.session.reviewed == 2
    keys.hold(Qt.Key_Y)                 # grades once; repeats do not reach the next card
    assert (view.session.reviewed, view.session.result.correct) == (3, 2)
    assert not view._revealed
    graded.append(view.word.text())
    keys.press(Qt.Key_Space)
    keys.press(Qt.Key_N)
    assert view.session.reviewed == 4 and view._ended
    stats = window.app.storage.load_stats("Book")
    assert [(stats[w]["box"], stats[w]["lapses"]) for w in graded] == [(2, 0), (1, 1), (2, 0), (1, 1)]


@DELIVERY
def test_study_buttons_still_work_by_tab_and_mouse(window, qt, delivery):
    keys = Keys(window, qt, delivery)
    view = _open_study(window, qt)
    keys.press(Qt.Key_Space)
    assert view._revealed and qt.focusWidget() is view   # no button took the next key

    keys.tab_to(view.good_btn)          # deliberate keyboard activation
    keys.press(Qt.Key_Space)
    assert (view.session.reviewed, view.session.result.correct) == (1, 1)
    assert qt.focusWidget() is view

    keys.tab_to(view.reveal_btn)
    keys.press(Qt.Key_Return)
    assert view._revealed and qt.focusWidget() is view
    keys.tab_to(view.bad_btn)
    keys.press(Qt.Key_Space)
    assert (view.session.reviewed, view.session.result.correct) == (2, 1)
    assert qt.focusWidget() is view

    keys.click(view.reveal_btn)
    assert view._revealed and view.session.reviewed == 2
    assert qt.focusWidget() is view
    keys.press(Qt.Key_Space)            # after a click, Space still does not grade
    assert view.session.reviewed == 2
    keys.click(view.good_btn)
    assert (view.session.reviewed, view.session.result.correct) == (3, 2)
    assert qt.focusWidget() is view
    keys.click(view.reveal_btn)
    keys.click(view.bad_btn)
    assert (view.session.reviewed, view.session.result.correct) == (4, 2)
    assert view._ended


def test_focused_study_card_describes_its_state(window, qt):
    keys = Keys(window, qt, "native window")
    view = _open_study(window, qt)
    assert view.accessibleName() == "Study card"

    word = view.word.text()
    description = view.accessibleDescription()
    assert "Card 1 of 4" in description and word in description and "Space" in description
    assert DEFINITIONS[word] not in description        # the answer stays hidden

    keys.press(Qt.Key_Space)
    description = view.accessibleDescription()
    assert word in description and DEFINITIONS[word] in description
    assert "Press J if you got it, or F if you missed it." in description

    keys.press(Qt.Key_J)
    word = view.word.text()
    description = view.accessibleDescription()
    assert description.startswith("Got it: box 1 to 2.")
    assert "Card 2 of 4" in description and word in description
    assert DEFINITIONS[word] not in description

    keys.press(Qt.Key_Space)
    keys.press(Qt.Key_F)
    assert view.accessibleDescription().startswith("Missed it: box 1 to 1.")
    assert "Card 3 of 4" in view.accessibleDescription()

    word = view.word.text()
    keys.press(Qt.Key_Space)

    def fail(*args):
        raise OSError("disk full")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(window.app.storage, "save_stats", fail)
        for _ in range(2):              # a retry does not repeat the notice
            keys.press(Qt.Key_J)
    description = view.accessibleDescription()
    assert description.startswith("Could not save review: disk full. " + word)
    assert description.count("disk full") == 1 and DEFINITIONS[word] in description
    assert view.session.reviewed == 2 and qt.focusWidget() is view
