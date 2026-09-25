"""Capture, revisit an open question, or explain a concept before seeing its source."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from ..core.learning import LearningStore, source_label
from .components import BaseComponent


def plain_label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


class CaptureDialog(QDialog):
    def __init__(self, ctx, module, quote="", source=None, item=None, parent=None):
        super().__init__(parent)
        self.ctx, self.module = ctx, module
        self.store = LearningStore(ctx.app.storage)
        self.item = item
        self.source = dict(item["source"] if item else source or {})
        self.setWindowTitle("Edit capture" if item else "Capture for learning")
        self.resize(620, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(f"{module} · {source_label(self.source)}"))
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("Concept to review", "concept")
        self.kind.addItem("Open question to revisit", "question")
        self.prompt = QLineEdit(item["prompt"] if item else "")
        self.prompt.setPlaceholderText("Why does this happen? How would I apply it?")
        self.quote = QPlainTextEdit(item["quote"] if item else quote)
        self.quote.setPlaceholderText("Paste a short passage, or trim the captured page to the relevant lines.")
        self.explanation = QPlainTextEdit(item["explanation"] if item else "")
        self.explanation.setPlaceholderText("Your explanation, example, or what you still don't understand (optional).")
        self.resolved = QCheckBox("Question resolved")
        self.resolved.setChecked(bool(item and item["resolved"]))
        form.addRow("Type", self.kind)
        form.addRow("Question", self.prompt)
        form.addRow("Source passage", self.quote)
        form.addRow("Your notes", self.explanation)
        form.addRow("", self.resolved)
        layout.addLayout(form, 1)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.kind.setCurrentIndex(1 if item and item["kind"] == "question" else 0)
        self._kind_changed()
        self.error = plain_label("")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._initial = self._values()

    def _kind_changed(self):
        self.resolved.setVisible(self.kind.currentData() == "question")

    def _values(self):
        return (self.kind.currentData(), self.prompt.text(), self.quote.toPlainText(),
                self.explanation.toPlainText(), self.resolved.isChecked())

    def save(self):
        kind, prompt, quote, explanation, resolved = self._values()
        try:
            self.store.save_capture(self.module, kind=kind, prompt=prompt, quote=quote,
                                    explanation=explanation, source=self.source, resolved=resolved,
                                    item_id=self.item["id"] if self.item else None,
                                    revision=self.item["revision"] if self.item else None)
        except Exception as error:
            self.error.setText(str(error))
            return
        self.ctx.learning_changed.emit()
        self.accept()

    def reject(self):
        if self._values() != self._initial and QMessageBox.question(
                self, "Unsaved capture", "Discard changes to this capture?",
                QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Discard:
            return
        super().reject()


class ReviewDialog(QDialog):
    def __init__(self, ctx, module, item, parent=None):
        super().__init__(parent)
        self.ctx, self.module, self.item = ctx, module, item
        self.setWindowTitle("Explain from memory")
        self.resize(620, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label(item["prompt"]))
        layout.addWidget(plain_label("Explain why it works, give an example, or say what you cannot recall. Then compare with the source and rate yourself."))
        self.answer = QPlainTextEdit()
        self.answer.setPlaceholderText("My explanation…")
        layout.addWidget(self.answer, 1)
        self.reveal = QPushButton("Compare with source")
        self.reveal.setEnabled(False)
        self.reveal.clicked.connect(self.show_source)
        self.answer.textChanged.connect(self._answer_changed)
        layout.addWidget(self.reveal)
        self.reference = QPlainTextEdit()
        self.reference.setReadOnly(True)
        self.reference.hide()
        layout.addWidget(self.reference, 1)
        self.error = plain_label("")
        layout.addWidget(self.error)
        row = QHBoxLayout()
        self.again = QPushButton("Needs work · 1 day")
        self.good = QPushButton("Understood")
        self.again.clicked.connect(lambda: self.rate("again"))
        self.good.clicked.connect(lambda: self.rate("good"))
        for button in (self.again, self.good):
            button.setEnabled(False)
            row.addWidget(button)
        layout.addLayout(row)
        self._revealed = False

    def _answer_changed(self):
        self.reveal.setEnabled(bool(self.answer.toPlainText().strip()) and not self._revealed)

    def show_source(self):
        if not self.answer.toPlainText().strip():
            return
        self._revealed = True
        self.answer.setReadOnly(True)
        self.reveal.setEnabled(False)
        item = self.item
        text = f"SOURCE · {source_label(item['source'])}\n\n{item['quote']}"
        if item["explanation"]:
            text += "\n\nYOUR CAPTURE NOTES\n\n" + item["explanation"]
        self.reference.setPlainText(text)
        self.reference.show()
        self.again.setEnabled(True)
        self.good.setEnabled(True)

    def rate(self, outcome):
        if not self._revealed:
            return
        try:
            item = LearningStore(self.ctx.app.storage).review(
                self.module, self.item["id"], self.item["revision"], self.answer.toPlainText(), outcome)
        except Exception as error:
            self.error.setText(str(error))
            return
        self.ctx.learning_changed.emit()
        self.ctx.log.emit("Explanation saved · " + LearningStore(self.ctx.app.storage).status(item))
        self.accept()

    def reject(self):
        if self.answer.toPlainText().strip() and QMessageBox.question(
                self, "Unfinished review", "Discard this explanation without changing the review schedule?",
                QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Discard:
            return
        super().reject()


class LearningComponent(BaseComponent):
    TITLE = "Learning"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.store = LearningStore(self.app.storage)
        self.items = {}
        self.module = None
        self.ctx.learning_changed.connect(self.refresh)
        layout = QVBoxLayout(self)
        self.summary = plain_label("")
        layout.addWidget(self.summary)
        self.entries = QListWidget()
        self.entries.currentItemChanged.connect(self._selected)
        self.entries.itemActivated.connect(self.edit)
        layout.addWidget(self.entries, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        row = QHBoxLayout()
        for title, method in (("+ Capture", self.capture), ("Edit", self.edit), ("Source", self.open_source), ("Delete", self.delete)):
            button = QPushButton(title)
            button.clicked.connect(method)
            row.addWidget(button)
        layout.addLayout(row)
        self.review_button = QPushButton("Review next due concept")
        self.review_button.clicked.connect(self.review)
        layout.addWidget(self.review_button)
        self.refresh()

    def focus_default(self):
        self.entries.setFocus()

    def selected(self):
        row = self.entries.currentItem()
        return self.items.get(row.data(Qt.UserRole)) if row else None

    def refresh(self):
        old = self.selected()
        old_id = old["id"] if old and self.module == self.app.current_module else None
        self.module = self.app.current_module
        self.entries.clear()
        self.items = {}
        self.details.clear()
        self.review_button.setEnabled(False)
        if not self.module:
            self.summary.setText("Select a module, then capture a passage from a reader or paste one here.")
            return
        try:
            self.items = self.store.load(self.module)
            due = self.store.due(self.items)
        except Exception as error:
            self.summary.setText(str(error))
            return
        open_count = sum(i["kind"] == "question" and not i["resolved"] for i in self.items.values())
        self.summary.setText(f"{len(due)} concepts due · {open_count} open questions\nCapture a passage → explain from memory → compare → revisit.")
        self.review_button.setEnabled(bool(due))
        ordered = sorted(self.items.values(), key=lambda i: (
            not (i["kind"] == "question" and not i["resolved"]), -i["created"]))
        for item in ordered:
            row = QListWidgetItem(f"{self.store.status(item)} · {item['prompt']}")
            row.setData(Qt.UserRole, item["id"])
            self.entries.addItem(row)
            if item["id"] == old_id:
                self.entries.setCurrentItem(row)

    def _selected(self, *_):
        item = self.selected()
        if not item:
            self.details.clear()
            return
        text = f"{source_label(item['source'])}\n\n{item['quote']}\n\nYour notes\n{item['explanation']}"
        if item["attempts"]:
            last = item["attempts"][-1]
            text += f"\n\nLast explanation ({last['rating']})\n{last['answer']}"
        self.details.setPlainText(text)

    def capture(self):
        if self.module:
            CaptureDialog(self.ctx, self.module, parent=self).exec()

    def edit(self, *_):
        item = self.selected()
        if item:
            CaptureDialog(self.ctx, self.module, item=item, parent=self).exec()

    def open_source(self):
        item = self.selected()
        if item:
            self.ctx.source_requested.emit(self.module, item["source"])

    def delete(self):
        item = self.selected()
        if not item or QMessageBox.question(self, "Delete capture", "Delete this capture and its review history?") != QMessageBox.Yes:
            return
        try:
            self.store.delete(self.module, item["id"], item["revision"])
        except Exception as error:
            self.ctx.log.emit(str(error))
            return
        self.ctx.learning_changed.emit()

    def review(self):
        if not self.module:
            return
        # Take a fresh snapshot; another pane may have reviewed the same entry.
        self.refresh()
        due = self.store.due(self.items)
        if due:
            # Hide the detail pane before opening the recall prompt.
            self.entries.clearSelection()
            self.details.clear()
            ReviewDialog(self.ctx, self.module, self.items[due[0]], self).exec()
