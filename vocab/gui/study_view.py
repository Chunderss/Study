"""The study view: a focused flashcard experience.

Drives a StudySession step by step from the Qt event loop (no blocking). Two
modes mirror the terminal:
  * reveal   — show word, click Reveal, self-grade Got it / Missed it
  * mechanical — type the definition, judge scores it, then continue

Keyboard-first (matches the command-driven spirit):
  Space / Enter = reveal ;  J / Y = got it ;  F / N = missed it ;  Esc = end
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from . import theme


class AnswerInput(QLineEdit):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            event.accept()
            if not event.isAutoRepeat():
                self.returnPressed.emit()
            return
        super().keyPressEvent(event)


class StudyView(QWidget):
    TITLE = "Study"
    finished = Signal(str)   # emits the session summary text when done

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self.session = session
        self._revealed = False
        self._awaiting_next = False
        self._ended = False
        self.setFocusPolicy(Qt.StrongFocus)
        self._build()
        self._load_card()

    def refresh(self) -> None:
        # study is a live session; nothing external to reload
        pass

    def focus_default(self) -> None:
        if self.session.mechanical and not self._awaiting_next:
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
        self.reveal_btn = QPushButton("Reveal  (Space)")
        self.reveal_btn.setDefault(True)
        self.reveal_btn.clicked.connect(self._reveal)

        self.good_btn = QPushButton("Got it  (J)")
        self.good_btn.setObjectName("Good")
        self.good_btn.clicked.connect(lambda: self._grade(True))

        self.bad_btn = QPushButton("Missed it  (F)")
        self.bad_btn.setObjectName("Bad")
        self.bad_btn.clicked.connect(lambda: self._grade(False))

        self.next_btn = QPushButton("Continue  (Enter)")
        self.next_btn.clicked.connect(self._continue)
        self.next_btn.hide()
        self.end_btn = QPushButton("End  (Esc)")
        self.end_btn.clicked.connect(self._end)

        btns.addWidget(self.end_btn)
        btns.addStretch(1)
        btns.addWidget(self.next_btn)
        btns.addWidget(self.reveal_btn)
        btns.addWidget(self.bad_btn)
        btns.addWidget(self.good_btn)
        root.addLayout(btns)

        # feedback line (box movement / judge score)
        self.feedback = QLabel("")
        self.feedback.setAlignment(Qt.AlignCenter)
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
        self._awaiting_next = False
        self.next_btn.hide()
        self.input.setEnabled(True)
        self.counter.setText(f"{view.index} / {view.total}")
        self.progress.setValue(view.index - 1)
        self.list_tag.setText(view.home_list)
        self.word.setText(view.word)
        self.answer.setText("")
        self.feedback.setText("")

        if self.session.mechanical:
            self.input.show()
            self.input.clear()
            self.input.setFocus()
            self.reveal_btn.hide()
            self.good_btn.hide()
            self.bad_btn.hide()
        else:
            self.input.hide()
            self.reveal_btn.show()
            self.reveal_btn.setEnabled(True)
            self.good_btn.show()
            self.bad_btn.show()
            self.good_btn.setEnabled(False)
            self.bad_btn.setEnabled(False)
            self.reveal_btn.setFocus()

    def _reveal(self) -> None:
        if self._revealed or self.session.done:
            return
        view = self.session.current()
        pos = f"{view.pos}:  " if view.pos else ""
        text = f"{pos}{view.definition}"
        if view.example:
            text += f"\n\n“{view.example}”"
        self.answer.setText(text)
        self._revealed = True
        self.reveal_btn.setEnabled(False)
        self.good_btn.setEnabled(True)
        self.bad_btn.setEnabled(True)
        self.good_btn.setFocus()

    def _grade(self, correct: bool) -> None:
        if not self._revealed or self.session.done:
            return
        outcome = self.session.answer(correct)
        self.progress.setValue(self.session.reviewed)
        self._load_card()
        self._show_box_feedback(outcome)

    def _submit_typed(self) -> None:
        if self.session.done or not self.session.mechanical or self._awaiting_next:
            return
        typed = self.input.text().strip()
        if not typed:
            return
        outcome = self.session.answer_text(typed)
        view_def = outcome.reference
        verdict = "correct" if outcome.correct else "not quite"
        self.answer.setText(f"reference:  {view_def}")
        self.feedback.setText(
            f"score {outcome.score:.0%} — {outcome.feedback}   ·   "
            f"box {outcome.box_before} → {outcome.box_after} ({verdict})")
        self._awaiting_next = True
        self.input.setEnabled(False)
        self.progress.setValue(self.session.reviewed)
        self.next_btn.show()
        self.next_btn.setFocus()

    def _continue(self):
        if self._awaiting_next:
            self._awaiting_next = False
            self._load_card()

    def _show_box_feedback(self, outcome) -> None:
        arrow = "↑" if outcome.box_after > outcome.box_before else (
            "↓" if outcome.box_after < outcome.box_before else "→")
        color = theme.GOOD if outcome.correct else theme.BAD
        self.feedback.setText(
            f"box {outcome.box_before} {arrow} {outcome.box_after}")
        self.feedback.setStyleSheet(f"color: {color};")

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
        if key == Qt.Key_Escape:
            self._end()
            return
        if self.session.mechanical:
            if self._awaiting_next and key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                self._continue()
                return
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
