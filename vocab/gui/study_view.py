"""The study view: a focused flashcard experience.

Drives a StudySession step by step from the Qt event loop (no blocking). Two
modes mirror the terminal:
  * reveal   — show word, click Reveal, self-grade Got it / Missed it
  * mechanical — type the definition; the judge suggests a verdict next to the
    reference, and Got it / Missed it records the final one

Keyboard-first (matches the command-driven spirit):
  Space / Enter = reveal ;  J / Y = got it ;  F / N = missed it ;  Esc = end
  mechanical, once judged: Enter = the judge's verdict ; J / Y, F / N as above
These keys count alone or with Shift; with Ctrl, Alt or Meta they are left to
shortcuts, so a reflex Ctrl+F or Ctrl+Y never records an answer, and neither
does Ctrl+Enter or Ctrl+Space on a Tab-focused button.
After a card change, a reveal or a judgement the view itself keeps keyboard
focus (a new mechanical card gives it to the answer box), so a second Space
never lands on a grade button. The buttons stay reachable with Tab and the
mouse; Space or Enter activates the focused one. Held keys act once.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from . import theme
from ..study.session import StudyCardUnavailable


class AnswerInput(QLineEdit):
    """Enter submits once. A key still held from the previous card (J to
    record a verdict) does not type into the next answer as it repeats."""

    _fresh_press = True      # False from focus-in until a key is newly pressed

    def focusInEvent(self, event):
        self._fresh_press = False
        super().focusInEvent(event)

    def keyPressEvent(self, event):
        if event.isAutoRepeat() and not self._fresh_press:
            event.accept()
            return
        self._fresh_press = True
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            if not event.isAutoRepeat():
                self.returnPressed.emit()
            return
        super().keyPressEvent(event)


# Keys with these held are shortcuts (Ctrl+F, Ctrl+Enter), never study answers.
# Shift (Caps Lock habits) and the keypad stay plain keys.
CHORD_MODIFIERS = Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier


class StudyButton(QPushButton):
    """Return or Enter activates a focused button, as Space does. Outside a
    dialog QPushButton takes them only as the default button; any other button
    would pass them on to the card, where Enter reveals. A held key acts once.
    With Ctrl, Alt or Meta held, Return, Enter and Space do not click it."""

    def keyPressEvent(self, event):
        if (event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)
                and event.modifiers() & CHORD_MODIFIERS):
            event.ignore()      # left to shortcuts, as on the card
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            if not event.isAutoRepeat():
                self.click()
            return
        super().keyPressEvent(event)


class StudyView(QWidget):
    TITLE = "Study"
    GOOD_LABEL, BAD_LABEL = "Got it  (J)", "Missed it  (F)"
    finished = Signal(str)   # emits the session summary text when done

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._revealed = False
        self._ended = False
        self._card_description = ""
        self.setFocusPolicy(Qt.StrongFocus)
        # The view holds focus in reveal mode, so screen readers announce it
        # instead of a button; its description follows the card (_describe).
        self.setAccessibleName("Study card")
        self._build()
        self._load_card()

    def refresh(self) -> None:
        # study is a live session; nothing external to reload
        pass

    def focusInEvent(self, event) -> None:
        # A click on the card, or on a verdict button before there is a
        # judgement, must not take the keys away from the answer box. Shift+Tab
        # from the answer box stops here on its way out of the pane: sending
        # it back would trap the keyboard.
        if (self.session.mechanical and self.session.judgement is None
                and self.input.isEnabled() and event.reason() != Qt.BacktabFocusReason):
            self.input.setFocus()
            return
        super().focusInEvent(event)

    def focus_default(self) -> None:
        if self.session.mechanical and self.session.judgement is None:
            self.input.setFocus()
        else:
            self.setFocus()

    # ---- layout ---------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 28, 40, 28)
        root.setSpacing(16)

        # top: session label + progress
        top = QHBoxLayout()
        self.label = QLabel(self.session.label)
        self.label.setObjectName("Muted")
        self.counter = QLabel("")
        self.counter.setObjectName("Muted")
        self.counter.setAlignment(Qt.AlignRight)
        top.addWidget(self.label)
        top.addWidget(self.counter, 1)
        root.addLayout(top)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximum(max(1, self.session.total))
        root.addWidget(self.progress)

        root.addStretch(1)

        # center: the card
        self.list_tag = QLabel("")
        self.list_tag.setObjectName("Muted")
        self.list_tag.setAlignment(Qt.AlignCenter)
        root.addWidget(self.list_tag)

        self.word = QLabel("")
        self.word.setAlignment(Qt.AlignCenter)
        self.word.setStyleSheet(f"font-size: 48px; font-weight: bold; color: {theme.INK};")
        self.word.setWordWrap(True)
        root.addWidget(self.word)

        self.answer = QLabel("")
        self.answer.setTextFormat(Qt.PlainText)
        self.word.setTextFormat(Qt.PlainText)
        self.answer.setAlignment(Qt.AlignCenter)
        self.answer.setWordWrap(True)
        self.answer.setStyleSheet(f"font-size: 18px; color: {theme.INK_DIM};")
        self.answer.setMinimumHeight(90)
        root.addWidget(self.answer)

        # mechanical-mode input (hidden in reveal mode)
        self.input = AnswerInput()
        self.input.setPlaceholderText("Type the definition, then press Enter...")
        self.input.returnPressed.connect(self._submit_typed)
        self.input.hide()
        root.addWidget(self.input)

        root.addStretch(1)

        # bottom: action buttons
        btns = QHBoxLayout()
        self.reveal_btn = StudyButton("Reveal  (Space)")
        self.reveal_btn.setDefault(True)
        self.reveal_btn.clicked.connect(self._reveal)

        self.good_btn = StudyButton(self.GOOD_LABEL)
        self.good_btn.setObjectName("Good")
        self.good_btn.clicked.connect(lambda: self._grade(True))

        self.bad_btn = StudyButton(self.BAD_LABEL)
        self.bad_btn.setObjectName("Bad")
        self.bad_btn.clicked.connect(lambda: self._grade(False))

        self.end_btn = StudyButton("End  (Esc)")
        self.end_btn.clicked.connect(self._end)

        btns.addWidget(self.end_btn)
        btns.addStretch(1)
        btns.addWidget(self.reveal_btn)
        btns.addWidget(self.bad_btn)
        btns.addWidget(self.good_btn)
        root.addLayout(btns)

        # feedback line (box movement / judge score)
        self.feedback = QLabel("")
        self.feedback.setAlignment(Qt.AlignCenter)
        self.feedback.setWordWrap(True)   # long judge feedback must not widen the view
        self.feedback.setObjectName("Muted")
        self.feedback.setMinimumHeight(20)
        root.addWidget(self.feedback)

    # ---- card lifecycle -------------------------------------------------
    def _load_card(self) -> None:
        if self.session.done:
            self._end()
            return
        view = self.session.current()
        self._revealed = False
        self.input.setEnabled(True)
        self.good_btn.setText(self.GOOD_LABEL)
        self.bad_btn.setText(self.BAD_LABEL)
        self.counter.setText(f"{view.index} / {view.total}")
        self.progress.setValue(view.index - 1)
        self.list_tag.setText(view.home_list)
        self.word.setText(view.word)
        self.answer.setText("")
        self.feedback.setText("")

        self.good_btn.setEnabled(False)
        self.bad_btn.setEnabled(False)
        if self.session.mechanical:
            self.input.show()
            self.input.clear()
            self.input.setFocus()
            self.reveal_btn.hide()
            self._describe(f"Card {view.index} of {view.total}: {view.word}. "
                           "Type the definition, then press Enter.")
        else:
            self.input.hide()
            self.reveal_btn.show()
            self.reveal_btn.setEnabled(True)
            self._describe(f"Card {view.index} of {view.total}: {view.word}. "
                           "Press Space to reveal the answer.")
            # A focused button clicks on Space or Enter, so it would turn the
            # next key into an action. Keys go to the view (keyPressEvent);
            # Tab and the mouse still reach the buttons.
            self.setFocus()

    def _reveal(self) -> None:
        if self._revealed or self.session.done:
            return
        view = self.session.current()
        if len(view.senses) > 1:  # several senses studied: show them all
            parts = []
            for number, sense in enumerate(view.senses, 1):
                pos = f"{sense.pos}:  " if sense.pos else ""
                parts.append(f"{number}. {pos}{sense.definition}"
                             + (f"\n“{sense.example}”" if sense.example else ""))
            text = "\n\n".join(parts)
        else:
            pos = f"{view.pos}:  " if view.pos else ""
            text = f"{pos}{view.definition}"
            if view.example:
                text += f"\n\n“{view.example}”"
        self.answer.setText(text)
        self._revealed = True
        self.reveal_btn.setEnabled(False)
        self.good_btn.setEnabled(True)
        self.bad_btn.setEnabled(True)
        self._describe(f"{view.word}. {text}\n\n"
                       "Press J if you got it, or F if you missed it.")
        # Not on "Got it": a second Space or Enter would grade the card. This
        # comes after disabling reveal_btn, which moves its focus elsewhere first.
        self.setFocus()

    def _can_grade(self) -> bool:
        if self.session.done:
            return False
        if self.session.mechanical:
            return self.session.judgement is not None
        return self._revealed

    def _grade(self, correct: bool) -> None:
        if not self._can_grade():
            return
        outcome = self._record_answer(self.session.answer, correct)
        if outcome is None:
            return
        self.progress.setValue(self.session.reviewed)
        self._load_card()
        self._show_box_feedback(outcome)

    def _accept_suggestion(self) -> None:
        judgement = self.session.judgement
        if judgement is not None and judgement.suggested is not None:
            self._grade(judgement.suggested)

    def _submit_typed(self) -> None:
        if (self.session.done or not self.session.mechanical
                or self.session.judgement is not None):
            return
        typed = self.input.text().strip()
        if not typed:
            return
        j = self.session.judge_text(typed)
        view = self.session.current()
        self.answer.setText(f"reference:  {j.reference}")
        self.feedback.setStyleSheet("")
        if j.suggested is None:
            self.feedback.setText(j.feedback)
            keys = "Press J if you got it, or F if you missed it."
        else:
            self.feedback.setText(f"score {j.score:.0%} — {j.feedback}")
            if j.suggested:
                self.good_btn.setText("Got it  (J / Enter)")
            else:
                self.bad_btn.setText("Missed it  (F / Enter)")
            keys = (f"Press Enter to keep the judge's verdict, "
                    f"{'Got it' if j.suggested else 'Missed it'}; "
                    "J if you got it, or F if you missed it.")
        self.input.setEnabled(False)
        self.good_btn.setEnabled(True)
        self.bad_btn.setEnabled(True)
        self._describe(f"{view.word}. You wrote: {j.typed}. Reference: {j.reference}. "
                       f"{self.feedback.text()}\n\n{keys}")
        # Not on a button: Space there would record a verdict.
        self.setFocus()

    def _record_answer(self, answer, value):
        try:
            return answer(value)
        except StudyCardUnavailable as error:
            self._load_card()
            self.feedback.setText(str(error))
            self._describe(notice=str(error))
        except (OSError, ValueError) as error:
            # Keep this card and the typed answer available for a save retry.
            self.feedback.setText(f"Could not save review: {error}")
            self._describe(notice=self.feedback.text())
        return None

    def _show_box_feedback(self, outcome) -> None:
        arrow = "↑" if outcome.box_after > outcome.box_before else (
            "↓" if outcome.box_after < outcome.box_before else "→")
        color = theme.GOOD if outcome.correct else theme.BAD
        overruled = "   ·   you overruled the judge" if outcome.overruled else ""
        self.feedback.setText(
            f"box {outcome.box_before} {arrow} {outcome.box_after}{overruled}")
        self.feedback.setStyleSheet(f"color: {color};")
        verdict = "Got it" if outcome.correct else "Missed it"
        notice = f"{verdict}: box {outcome.box_before} to {outcome.box_after}"
        if outcome.overruled:
            notice += ". You overruled the judge"
        self._describe(notice=notice)

    def _describe(self, card: str | None = None, notice: str = "") -> None:
        """Keep the accessible description of the focused card current: the
        card text, after a notice about the last answer if there is one. A
        mechanical card waiting for its answer also describes itself on the
        answer box, which has the focus then."""
        if card is not None:
            self._card_description = card
        if notice and not notice.endswith((".", "!", "?")):
            notice += "."      # error texts end without a full stop to pause at
        text = f"{notice} {self._card_description}" if notice else self._card_description
        self.setAccessibleDescription(text)
        if self.session.mechanical and self.session.judgement is None:
            self.input.setAccessibleDescription(text)

    def _end(self) -> None:
        if self._ended:
            return
        self._ended = True
        summary = self.session.finish()
        self.finished.emit(summary)

    # ---- keyboard -------------------------------------------------------
    def keyPressEvent(self, e) -> None:
        key = e.key()
        if e.isAutoRepeat():
            e.accept()
            return
        if e.modifiers() & CHORD_MODIFIERS:
            # A chord such as Ctrl+F or Ctrl+Y is a shortcut, never an answer.
            super().keyPressEvent(e)
            return
        if key == Qt.Key_Escape:
            self._end()
            return
        if self.session.mechanical:
            judged = self.session.judgement is not None
            if judged and key in (Qt.Key_Return, Qt.Key_Enter):
                self._accept_suggestion()
            elif judged and key in (Qt.Key_J, Qt.Key_Y):
                self._grade(True)
            elif judged and key in (Qt.Key_F, Qt.Key_N):
                self._grade(False)
            else:
                super().keyPressEvent(e)
            return
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter) and not self._revealed:
            self._reveal()
        elif self._revealed and key in (Qt.Key_J, Qt.Key_Y):
            self._grade(True)
        elif self._revealed and key in (Qt.Key_F, Qt.Key_N):
            self._grade(False)
        else:
            super().keyPressEvent(e)
