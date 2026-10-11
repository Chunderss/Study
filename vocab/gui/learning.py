"""Capture, revisit an open question, or explain a concept before seeing its source."""
import time

from PySide6.QtCore import QEvent, QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from ..core.learning import (SEARCHED_FIELDS, STATUS_FILTERS, CaptureFilter, LearningStore,
                             search_fields, source_choice_label, source_choices, source_label)
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
        self.saved = None  # the capture as saved, once Save succeeds
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
            self.saved = self.store.save_capture(
                self.module, kind=kind, prompt=prompt, quote=quote,
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
        self.again = QPushButton("Needs work · tomorrow")
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


class _CaptureList(QListWidget):
    def focusInEvent(self, event):
        super().focusInEvent(event)
        # Taking keyboard focus makes the first row current without
        # highlighting it, and nothing draws that row. Drop it, so the first
        # arrow key highlights the first capture instead of the second.
        row = self.currentItem()
        if row is not None and not row.isSelected():
            self.selectionModel().clearCurrentIndex()


class _SearchField(QLineEdit):
    """Esc clears the search; Down or Enter moves on to the results."""
    to_results = Signal()

    def keyPressEvent(self, event):
        plain = not (event.modifiers() & ~Qt.KeypadModifier)
        if plain and event.key() == Qt.Key_Escape and self.text():
            self.clear()
            return
        if plain and event.key() in (Qt.Key_Down, Qt.Key_Return, Qt.Key_Enter):
            self.to_results.emit()
            return
        super().keyPressEvent(event)


_HINT = "Capture a passage → explain from memory → compare → revisit."


class LearningComponent(BaseComponent):
    TITLE = "Learning"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.store = LearningStore(self.app.storage)
        self.items = {}
        self.module = None
        self._list_generation = 0  # deferred scrolls belong to one version of the list
        self.filter = CaptureFilter()
        self._source_keys = [None]   # source_filter index -> source_choices key
        self._counts = (0, 0)        # concepts due, open questions
        self._fields = {}            # capture id -> (its SEARCHED_FIELDS, search_fields)
        self._problem = None         # why nothing is listed: no module, an unreadable file
        self.ctx.learning_changed.connect(self.refresh)
        layout = QVBoxLayout(self)
        self.summary = plain_label("")
        layout.addWidget(self.summary)
        self.search = _SearchField()
        self.search.setPlaceholderText("Search questions, passages and notes (Ctrl+F)")
        self.search.setAccessibleName("Search captures")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filters_changed)
        self.search.to_results.connect(self._to_results)
        layout.addWidget(self.search)
        filters = QHBoxLayout()
        self.source_filter = QComboBox()
        self.source_filter.setAccessibleName("Filter by source")
        self.source_filter.setToolTip("Show captures from one document, chapter or page")
        # A long file name must not widen the pane.
        self.source_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.source_filter.setMinimumContentsLength(10)
        self.source_filter.addItem("All sources")
        self.status_filter = QComboBox()
        self.status_filter.setAccessibleName("Filter by status")
        for key, text in STATUS_FILTERS.items():
            self.status_filter.addItem(text, key)
        self.clear_filters = QPushButton("Clear filters")
        self.clear_filters.setEnabled(False)
        self.clear_filters.clicked.connect(self._clear_filters)
        self.source_filter.currentIndexChanged.connect(self._filters_changed)
        self.status_filter.currentIndexChanged.connect(self._filters_changed)
        filters.addWidget(self.source_filter, 1)
        filters.addWidget(self.status_filter, 1)
        filters.addWidget(self.clear_filters)
        layout.addLayout(filters)
        self.entries = _CaptureList()
        self.entries.setUniformItemSizes(True)  # one line per capture: quick to filter
        self.entries.currentItemChanged.connect(self._selected)
        self.entries.itemSelectionChanged.connect(self._selected)
        self.entries.itemActivated.connect(self.edit)
        layout.addWidget(self.entries, 1)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        layout.addWidget(self.details, 1)
        row = QHBoxLayout()
        self.target_buttons = []  # act on the highlighted capture
        for title, method in (("+ Capture", self.capture), ("Edit", self.edit), ("Source", self.open_source), ("Delete", self.delete)):
            button = QPushButton(title)
            button.clicked.connect(method)
            row.addWidget(button)
            if title != "+ Capture":
                self.target_buttons.append(button)
        layout.addLayout(row)
        self.review_button = QPushButton("Review next due concept")
        self.review_button.setToolTip("Reviews this module's next due concept; "
                                      "the search and filters do not limit it.")
        self.review_button.clicked.connect(self.review)
        layout.addWidget(self.review_button)
        chain = [self.search, self.source_filter, self.status_filter, self.clear_filters, self.entries]
        for first, second in zip(chain, chain[1:]):
            QWidget.setTabOrder(first, second)
        self.refresh()
        self._selected()

    def focus_default(self):
        self.entries.setFocus()

    def event(self, event):
        # Find (Ctrl+F) anywhere in this pane goes to its search box. It is
        # handled as a key event the focused child passes up, not a
        # QShortcut: with two Learning panes, PySide6 6.11 finds the panes'
        # shortcuts ambiguous and runs neither.
        if (event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress)
                and event.matches(QKeySequence.Find)):
            event.accept()
            if event.type() == QEvent.KeyPress:
                self.focus_search()
            return True
        return super().event(event)

    def focus_search(self):
        self.search.setFocus(Qt.ShortcutFocusReason)
        self.search.selectAll()

    def _to_results(self):
        """Down or Enter in the search box: on to the list, highlighting the
        first capture shown unless a shown capture is already highlighted."""
        self.entries.setFocus(Qt.OtherFocusReason)
        if self.selected() is None and self.entries.count():
            self.entries.setCurrentRow(0)

    def selected(self):
        """The highlighted capture. The list can also have a current row that
        is not highlighted (a row takes keyboard focus, or Ctrl+click clears
        it); Edit, Source and Delete never act on that one."""
        row = self.entries.currentItem()
        return self.items.get(row.data(Qt.UserRole)) if row and row.isSelected() else None

    def navigation_state(self):
        item = self.selected()
        return {"module": self.module, "capture_id": item["id"] if item else None,
                "entries_scroll": self.entries.verticalScrollBar().value(),
                "details_scroll": self.details.verticalScrollBar().value(),
                "filters": self.filter.state()}

    def restore_navigation_state(self, state):
        if not isinstance(state, dict) or state.get("module") != self.module:
            return
        # The saved list position belongs to the filtered list.
        self._show_filter(CaptureFilter.from_state(state.get("filters")))
        self._fill_source_filter()
        self._populate(None)
        capture_id = state.get("capture_id")
        for index in range(self.entries.count()):
            row = self.entries.item(index)
            if row.data(Qt.UserRole) == capture_id:
                self.entries.setCurrentItem(row)
                break
        generation = self._list_generation

        def restore_scroll():
            selected = self.selected()
            if (generation != self._list_generation or self.module != state.get("module") or
                    (selected["id"] if selected else None) != capture_id):
                return
            for key, widget in (("entries_scroll", self.entries), ("details_scroll", self.details)):
                value = state.get(key)
                if isinstance(value, int) and value >= 0:
                    widget.verticalScrollBar().setValue(value)

        # Scroll ranges settle after the component is inserted into its pane.
        QTimer.singleShot(0, self, restore_scroll)

    def refresh(self):
        old = self.selected()
        same_module = self.module is not None and self.module == self.app.current_module
        if not same_module:
            self._show_filter(CaptureFilter())  # another module starts unfiltered
            self._fields = {}
        old_id = old["id"] if old and same_module else None
        scroll = (self.entries.verticalScrollBar().value(),
                  self.details.verticalScrollBar().value()) if same_module else None
        # Selecting the same capture again must not scroll the list to it.
        self.entries.setAutoScroll(scroll is None)
        try:
            self._rebuild(old_id)
        finally:
            self.entries.setAutoScroll(True)
        if scroll is not None:
            self._keep_scroll(old_id, *scroll)
        else:
            self.entries.scrollToTop()  # a different module has its own list

    def _keep_scroll(self, capture_id, entries, details):
        """A refresh in the same module (a note saved, a word added, another
        capture changed) keeps the list where it was, and the passage too
        while the same capture is selected. entries None leaves the list."""
        module, generation = self.module, self._list_generation

        def apply():
            if self.module != module or self._list_generation != generation:
                return
            if entries is not None:
                self.entries.doItemsLayout()  # the list lays out its rows lazily
                self.entries.verticalScrollBar().setValue(entries)
            selected = self.selected()
            if capture_id is not None and (selected["id"] if selected else None) == capture_id:
                self.details.verticalScrollBar().setValue(details)
        apply()
        QTimer.singleShot(0, self, apply)  # scroll ranges can settle a moment later

    def _rebuild(self, old_id):
        self._list_generation += 1
        self.module = self.app.current_module
        self.entries.clear()
        self.items = {}
        self.details.clear()
        self.review_button.setEnabled(False)
        problem = None
        if not self.module:
            problem = "Select a module, then capture a passage from a reader or paste one here."
        else:
            try:
                self.items = self.store.load(self.module)
                due = self.store.due(self.items)
            except Exception as error:
                self.items = {}
                problem = str(error)
        # Change the filters' state only when it really changes: disabling
        # the focused search box would move the focus while the user types.
        self._set_filters_enabled(problem is None)
        self._fill_source_filter()
        self._problem = problem
        if problem is not None:
            self._show_summary()
            return
        self._counts = (len(due), sum(i["kind"] == "question" and not i["resolved"]
                                      for i in self.items.values()))
        self._fields = {key: value for key, value in self._fields.items() if key in self.items}
        self.review_button.setEnabled(bool(due))
        self._populate(old_id)

    def _populate(self, old_id):
        """List the captures the search and filters show, and highlight
        old_id again if it is among them."""
        self._list_generation += 1
        self.entries.clear()
        now = time.time()
        searching = bool(self.filter.words)
        ordered = sorted(self.items.values(), key=lambda i: (
            not (i["kind"] == "question" and not i["resolved"]), -i["created"]))
        for item in ordered:
            fields = self._search_fields(item) if searching else None
            if not self.store.matches(item, self.filter, now, fields):
                continue
            # One line per capture, whatever the question holds.
            row = QListWidgetItem(" ".join(f"{self.store.status(item, now)} · {item['prompt']}".split()))
            row.setData(Qt.UserRole, item["id"])
            self.entries.addItem(row)
            if item["id"] == old_id:
                self.entries.setCurrentItem(row)
        self._show_summary()

    def _search_fields(self, item):
        """search_fields(item), folded again only when the capture's text
        changed. The text is compared, not the revision: a module restored
        from a backup, or a learning.json put back from elsewhere, has the
        same capture ids and revisions as another copy, with other text."""
        text = tuple(item[field] for field in SEARCHED_FIELDS)
        cached = self._fields.get(item["id"])
        if cached is None or cached[0] != text:
            cached = self._fields[item["id"]] = (text, search_fields(item))
        return cached[1]

    def _show_summary(self):
        if self._problem is not None:
            # Restoring the saved filters lists nothing; the reason stays.
            self.summary.setText(self._problem)
            return
        total, shown = len(self.items), self.entries.count()
        if not (self.filter.active and total):
            hint = _HINT
        elif shown:
            hint = f"Showing {shown} of {total} captures."
        else:
            hint = f"No captures match. Clear filters to show all {total}."
        due, open_count = self._counts
        self.summary.setText(f"{due} concepts due · {open_count} open questions\n{hint}")
        self.clear_filters.setEnabled(self.search.isEnabled() and self.filter.active)

    def _set_filters_enabled(self, enabled):
        for widget in (self.search, self.source_filter, self.status_filter):
            if widget.isEnabled() != enabled:
                widget.setEnabled(enabled)
        self.clear_filters.setEnabled(enabled and self.filter.active)

    def _fill_source_filter(self):
        """List the sources of the current captures, keeping the chosen one."""
        keys = source_choices(self.items, keep=self.filter.source)
        with QSignalBlocker(self.source_filter):
            if keys != self._source_keys[1:]:
                # Refilling closes the menu if it is open, so only on a change.
                self.source_filter.clear()
                self.source_filter.addItem("All sources")
                for key in keys:
                    self.source_filter.addItem(source_choice_label(key))
                self._source_keys = [None] + keys
            self.source_filter.setCurrentIndex(self._source_keys.index(self.filter.source))

    def _show_filter(self, capture_filter):
        """Show capture_filter's search text and status, without applying
        it; _fill_source_filter shows its source."""
        self.filter = capture_filter
        with QSignalBlocker(self.search), QSignalBlocker(self.status_filter):
            if self.search.text() != capture_filter.query:
                self.search.setText(capture_filter.query)
            self.status_filter.setCurrentIndex(list(STATUS_FILTERS).index(capture_filter.status))

    def _filters_changed(self, *_):
        index = self.source_filter.currentIndex()
        old, self.filter = self.filter, CaptureFilter(
            self.search.text(),
            self._source_keys[index] if 0 <= index < len(self._source_keys) else None,
            self.status_filter.currentData() or "all")
        # A space typed between words, say, changes nothing that is shown.
        if (old.words, old.source, old.status) != (self.filter.words, self.filter.source,
                                                   self.filter.status):
            self._apply_filter()

    def _apply_filter(self):
        old = self.selected()
        details = self.details.verticalScrollBar().value()
        self._populate(old["id"] if old else None)
        # A capture the filters hide stops being the target, and showing it
        # again does not highlight it again.
        if old is not None and self.selected() is not None:
            self._keep_scroll(old["id"], None, details)
        else:
            self.entries.scrollToTop()

    def _clear_filters(self):
        self._show_filter(CaptureFilter())
        self._fill_source_filter()
        self._apply_filter()
        self.search.setFocus(Qt.OtherFocusReason)

    def _report_hidden(self, dialog):
        """Say so when a capture just saved here is hidden by the filters."""
        saved = dialog.saved
        if saved is None or saved["id"] not in self.items:
            return
        shown = any(self.entries.item(row).data(Qt.UserRole) == saved["id"]
                    for row in range(self.entries.count()))
        if not shown:
            self.ctx.log.emit("Capture saved. The current search or filters hide it; "
                              "Clear filters shows it.")

    def _selected(self, *_):
        item = self.selected()
        for button in self.target_buttons:
            button.setEnabled(item is not None)
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
            dialog = CaptureDialog(self.ctx, self.module, parent=self)
            dialog.exec()
            self._report_hidden(dialog)

    def edit(self, *_):
        item = self.selected()
        if item:
            dialog = CaptureDialog(self.ctx, self.module, item=item, parent=self)
            dialog.exec()
            self._report_hidden(dialog)

    def open_source(self):
        item = self.selected()
        if item:
            source = dict(item["source"])
            source.update(capture_id=item["id"], quote=item["quote"],
                          _learning_origin=self, _learning_return=self.navigation_state())
            self.ctx.source_requested.emit(self.module, source)

    def delete(self):
        item = self.selected()
        # The capture is named after the first line, so it always shows as plain text.
        if not item or QMessageBox.question(
                self, "Delete capture",
                f"Delete this capture and its review history?\n\n{item['prompt']}") != QMessageBox.Yes:
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
            # Hide the detail pane before opening the recall prompt, and leave
            # no current row: a refresh meanwhile must not select it again,
            # and afterwards Edit, Source and Delete wait for a new selection.
            self.entries.setCurrentItem(None)
            self.details.clear()
            ReviewDialog(self.ctx, self.module, self.items[due[0]], self).exec()
