"""Workspace components — the things that live inside panes.

Each component is a QWidget with:
  * TITLE            : str shown in the pane title bar
  * refresh()        : reload from the current module
  * focus_default()  : put keyboard focus on the primary control

Components talk only to WorkspaceContext (ctx). No component references another.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSplitter, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)

from ..cli.parser import CommandSyntaxError, parse
from ..cli.repl import Repl
from ..core.storage import ModuleNotFound
from . import theme
from .markdown_editor import MarkdownEditor


class BaseComponent(QWidget):
    TITLE = "Component"

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.app = ctx.app
        ctx.module_changed.connect(self.refresh)
        ctx.changed.connect(self.refresh)

    def refresh(self) -> None:
        ...

    def focus_default(self) -> None:
        self.setFocus()


# --------------------------------------------------------------------------- #
class WordList(QTextBrowser):
    """The Vocab list, each word a link to its senses (``word_opened`` gets
    its position). A click opens a word; from the keyboard, Up and Down move
    between the words and Enter opens the highlighted one. One Tab leaves
    the list, where QTextBrowser would first stop on every link. The
    highlighted word is the keyboard's place: it is shown while the list
    has focus and comes back after an update or a visit elsewhere."""

    def __init__(self, word_opened):
        super().__init__()
        self.setOpenLinks(False)
        self.setAccessibleDescription("Up and Down move between the words; Enter opens the "
                                      "senses of the highlighted word.")
        self._word_opened = word_opened
        self._links = []    # (start, end) of each word's link in the text, by position
        self.place = None   # the position of the highlighted word
        self.anchorClicked.connect(self._clicked)

    def set_links(self, place=None) -> None:
        """After setHtml: find the links, and highlight ``place`` again."""
        self._links, hrefs = [], []
        block = self.document().begin()
        while block.isValid():
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                href = fragment.charFormat().anchorHref()
                if href.startswith("sense:"):
                    start = fragment.position()
                    if hrefs and hrefs[-1] == href:  # one link in several fragments
                        start = self._links.pop()[0]
                        hrefs.pop()
                    self._links.append((start, fragment.position() + fragment.length()))
                    hrefs.append(href)
                fragments += 1
            block = block.next()
        self.place = None if place is None or not self._links else min(place, len(self._links) - 1)
        if self.hasFocus():
            self._show_place()

    def _highlighted(self):
        cursor = self.textCursor()
        span = (cursor.selectionStart(), cursor.selectionEnd())
        return self._links.index(span) if cursor.hasSelection() and span in self._links else None

    def _show_place(self) -> None:
        """Highlight the place without scrolling the list."""
        if self.place is None:
            return
        scroll = self.verticalScrollBar().value()
        self._select(self.place)
        self.verticalScrollBar().setValue(scroll)

    def _select(self, index: int) -> None:
        start, end = self._links[index]
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        self.setTextCursor(cursor)  # scrolls the word into view
        self.place = index

    def _reveal(self, index: int) -> None:
        """Highlight a word and scroll so the senses under it show too."""
        self._select(index)
        block = self.document().findBlock(self._links[index][0])
        rect = self.document().documentLayout().blockBoundingRect(block)
        scroll = self.verticalScrollBar()
        height = self.viewport().height()
        if rect.bottom() > scroll.value() + height:
            scroll.setValue(int(rect.bottom()) - height + 1)
        if rect.top() < scroll.value():
            scroll.setValue(int(rect.top()))

    def _clicked(self, url) -> None:
        try:
            index = int(url.toString().partition(":")[2])
        except ValueError:
            return
        if 0 <= index < len(self._links):
            self.place = index
            self._word_opened(index)

    def focusNextPrevChild(self, next):
        return QTextEdit.focusNextPrevChild(self, next)  # not QTextBrowser's link stops

    def keyPressEvent(self, event):
        key = event.key()
        plain = not event.modifiers() & ~Qt.KeypadModifier
        if plain and self._links and key in (Qt.Key_Down, Qt.Key_Up):
            current = self._highlighted()
            if current is None:
                target = 0 if self.place is None else self.place
            else:
                target = current + (1 if key == Qt.Key_Down else -1)
            if 0 <= target < len(self._links):
                self._reveal(target)
                event.accept()
                return
        elif plain and key in (Qt.Key_Return, Qt.Key_Enter):
            current = self._highlighted()
            if current is not None:
                event.accept()
                self._word_opened(current)
                return
        super().keyPressEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self._highlighted() is None and event.reason() != Qt.MouseFocusReason:
            self._show_place()

    def focusOutEvent(self, event):
        # Like a link Qt highlights, the place shows only while the list has
        # focus; a selection made with the mouse is left alone.
        if (self._highlighted() is not None
                and event.reason() not in (Qt.ActiveWindowFocusReason, Qt.PopupFocusReason)):
            cursor = self.textCursor()
            cursor.clearSelection()
            self.setTextCursor(cursor)
        super().focusOutEvent(event)


class VocabComponent(BaseComponent):
    TITLE = "Vocab"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        # Each word is a link to its senses: a click, or Up/Down to it and Enter.
        self.words = WordList(self._open_senses)
        self._linked = []           # the words the links name, by position
        self._linked_module = None  # the module those links belong to
        lay.addWidget(self.words, 1)
        row = QHBoxLayout()
        self._pending = None
        self._submitted = ""
        self.ctx.word_added.connect(self._added)
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("Add a word (Enter).  word :: your definition  skips the dictionary")
        self.add_input.returnPressed.connect(self._add)
        row.addWidget(self.add_input, 1)
        add_btn = self.add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)
        lay.addLayout(row)
        self.refresh()

    def focus_default(self) -> None:
        self.add_input.setFocus()

    def _add(self) -> None:
        raw = self.add_input.text().strip()
        if self._pending is not None or not raw:
            return
        if not self.app.current_module:
            self.ctx.log.emit("! Select or create a module first.")
            return
        word, manual = raw, None
        if "::" in raw:
            word, manual = [s.strip() for s in raw.split("::", 1)]
        try:
            self._submitted = raw
            self._pending = self.ctx.add_word(word, manual=manual)
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
            return  # retain input so a failed lookup can be corrected/retried
        if self._pending is None:
            self.add_input.clear()
        else:
            self.add_btn.setEnabled(False)

    def _added(self, token, success):
        if token != self._pending:
            return
        self._pending = None
        self.add_btn.setEnabled(True)
        if success and self.add_input.text().strip() == self._submitted:
            self.add_input.clear()

    def _show_text(self, text: str) -> None:
        self._linked, self._linked_module = [], None
        self.words.setPlainText(text)
        self.words.set_links()

    def _open_senses(self, index: int) -> None:
        """A word's link: show its senses in the sense picker."""
        if 0 <= index < len(self._linked):
            self.ctx.senses_requested.emit(self._linked_module, self._linked[index])

    def refresh(self) -> None:
        name = self.app.current_module
        if not name:
            self._show_text("No module selected.")
            return
        try:
            wl = self.app.storage.load_words(name)
        except ModuleNotFound:
            self._show_text(f"Module '{name}' is no longer available.")
            return
        except Exception as error:  # refresh runs on every change; never raise here
            self._show_text(f"Could not read this module's vocabulary.\n\n{error}")
            return
        if not wl.words:
            self._show_text("(no vocab yet) — add your first word below.")
            return
        # A refresh of the same module keeps the place in a long list: the
        # scroll position, and the highlighted word (where it was, if it went).
        scroll = self.words.verticalScrollBar()
        same = self._linked_module == name
        keep = scroll.value() if same else 0
        place = self.words.place if same else None
        placed = self._linked[place].strip().lower() if place is not None else None
        blocks, linked = [], []
        for key in sorted(wl.words):
            w = wl.words[key]
            studied = "".join(
                "<br>&nbsp;&nbsp;&nbsp;&nbsp;"
                + html.escape((f"[{sense.pos}] " if sense.pos else "") + sense.definition)
                for sense in w.kept_senses())  # one line per sense studied
            # The link names a position, so no word text goes into a URL.
            blocks.append(f'<p><a href="sense:{len(linked)}">{html.escape(w.word)}</a>'
                          f"{studied}</p>")
            linked.append(w.word)
        self._linked, self._linked_module = linked, name
        self.words.setHtml("".join(blocks))
        self.words.document().documentLayout().documentSize()  # lay out before scrolling
        keys = [word.strip().lower() for word in linked]
        self.words.set_links(keys.index(placed) if placed in keys else place)
        scroll.setValue(keep)


# --------------------------------------------------------------------------- #
class NotesComponent(BaseComponent):
    TITLE = "Notes"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self._current = None
        self._loaded_note = None
        self._buffer = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)
        # Outer splitter: note-picker | editor+preview. Flips to vertical
        # (picker on top) when the pane gets narrow, so the editor keeps room.
        self.outer = QSplitter(Qt.Horizontal)
        self.outer.setChildrenCollapsible(False)
        lay.addWidget(self.outer)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        self.note_list = QListWidget()
        self.note_list.currentTextChanged.connect(self._select)
        left.addWidget(self.note_list, 1)
        b = QHBoxLayout()
        new = QPushButton("+ Note"); new.clicked.connect(self._new)
        dele = QPushButton("Del"); dele.setObjectName("Bad"); dele.clicked.connect(self._delete)
        b.addWidget(new); b.addWidget(dele)
        left.addLayout(b)
        self._picker = QWidget(); self._picker.setLayout(left)
        self.outer.addWidget(self._picker)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel("(no note)", objectName="Muted")
        right.addWidget(self.title)
        self.markdown = MarkdownEditor()
        self.editor = self.markdown.editor
        self.editor.setPlaceholderText("Write Markdown here… (Ctrl+B, then s saves)")
        self.editor.textChanged.connect(self._dirty)
        right.addWidget(self.markdown, 1)
        srow = QHBoxLayout()
        self.dirty = QLabel("", objectName="Muted")
        preview_toggle = QPushButton("Preview")
        preview_toggle.setToolTip("Show/hide live preview (Ctrl+B, then r)")
        preview_toggle.clicked.connect(self.toggle_preview)
        save = QPushButton("Save"); save.clicked.connect(self.save)
        save.setToolTip("Save Markdown (Ctrl+B, then s)")
        srow.addWidget(self.dirty, 1)
        srow.addWidget(preview_toggle); srow.addWidget(save)
        right.addLayout(srow)
        self._editing_side = QWidget(); self._editing_side.setLayout(right)
        self.outer.addWidget(self._editing_side)
        self.outer.setStretchFactor(0, 0)
        self.outer.setStretchFactor(1, 1)
        self._apply_outer_orientation()
        self._set_editing(False)
        self.refresh()

    # keep the picker beside the editor when there's room, stacked when narrow
    _NARROW_W = 560

    def _apply_outer_orientation(self) -> None:
        narrow = self.width() < self._NARROW_W
        orientation = Qt.Vertical if narrow else Qt.Horizontal
        changed = self.outer.orientation() != orientation
        if narrow:
            self._picker.setMaximumWidth(16777215)
            self._picker.setMaximumHeight(150)

        else:
            self._picker.setMaximumHeight(16777215)
            self._picker.setMaximumWidth(200)
        if changed:
            self.outer.setOrientation(orientation)
            self.outer.setSizes([150, max(1, self.height() - 150)] if narrow
                                else [170, max(1, self.width() - 170)])
            self.outer.updateGeometry()
            self.layout().invalidate()
            self.layout().activate()

    def resizeEvent(self, e):
        self._apply_outer_orientation()
        super().resizeEvent(e)

    def focus_default(self) -> None:
        # keyboard-first: land on the note list so arrows+Enter pick a note
        self.note_list.setFocus()
        if self.note_list.count() and self.note_list.currentRow() < 0:
            self.note_list.setCurrentRow(0)

    def toggle_preview(self) -> None:
        self.markdown.toggle_preview()

    def _set_editing(self, on: bool) -> None:
        self.editor.setEnabled(on)

    def refresh(self) -> None:
        module = self.app.current_module
        if module is None and self._keep_orphaned_draft():
            return
        names = self.app.storage.note_names(module) if module else []
        # Keep deleted/external drafts accessible until explicitly discarded.
        names = sorted(set(names) | {note for (home, note), buffer in
                       self.ctx.notes.buffers.items() if home == module and buffer.dirty})
        self.note_list.blockSignals(True)
        self.note_list.clear()
        self.note_list.addItems(names)
        wanted = self._current if self._current in names else None
        if wanted:
            self.note_list.setCurrentRow(names.index(wanted))
        self.note_list.blockSignals(False)
        if wanted:
            self._select(wanted)
        else:
            self._detach()
            self._current = None
            self._loaded_note = None
            self.editor.clear()
            self.title.setText("(no note)")
            self.dirty.clear()
            self._set_editing(False)
            self.markdown.render(reset_scroll=True)

    def _keep_orphaned_draft(self) -> bool:
        """Keep an unsaved note on screen after its module vanished outside the
        app, so its text can still be copied. It stays until another module is
        selected; the draft itself remains tracked for the close prompt."""
        buffer = self._buffer
        if buffer is None or not buffer.dirty:
            return False
        try:
            if self.app.storage.exists(buffer.module):
                return False
        except OSError:
            return False
        self.note_list.blockSignals(True)
        self.note_list.clear()
        self.note_list.addItem(buffer.note)
        self.note_list.setCurrentRow(0)
        self.note_list.blockSignals(False)
        self.title.setText(f"{buffer.note} — module '{buffer.module}' was removed; "
                           "copy this text to keep it")
        return True

    def _detach(self):
        if self._buffer is not None:
            self._buffer.changed.disconnect(self._dirty)
            self._buffer = None
        # Never clear a shared document when clearing this view.
        from PySide6.QtGui import QTextDocument
        from PySide6.QtWidgets import QPlainTextDocumentLayout
        doc = QTextDocument(self.editor)
        doc.setDocumentLayout(QPlainTextDocumentLayout(doc))
        self.editor.setDocument(doc)

    def _select(self, note: str) -> None:
        if not note or not self.app.current_module:
            return
        identity = (self.app.current_module, note)
        try:
            buffer = self.ctx.notes.open(*identity)
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
            return
        if buffer is self._buffer:
            return  # preserve cursor, selection, undo stack and preview position
        self._detach()
        self._buffer = buffer
        self._current = note
        self._loaded_note = identity
        self.editor.setDocument(buffer.document)
        buffer.changed.connect(self._dirty)
        self.markdown.render(reset_scroll=True)
        self.title.setText(note)
        self._set_editing(True)
        self._dirty()

    def _new(self) -> None:
        if not self.app.current_module:
            self.ctx.log.emit("! Select or create a module first.")
            return
        name, ok = QInputDialog.getText(self, "New note", "Note name:")
        if not ok or not name.strip():
            return
        try:
            stem = self.app.storage.create_note(self.app.current_module, name.strip())
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
            return
        self._current = stem
        self.ctx.changed.emit()
        self.editor.setFocus()

    def _delete(self) -> None:
        if not self._loaded_note:
            return
        module, note = self._loaded_note
        if QMessageBox.question(self, "Delete note",
                                f"Delete '{note}', including any unsaved edits?") != QMessageBox.Yes:
            return
        storage = self.app.storage
        try:
            if storage._note_path(module, note, exact=True).exists():
                storage.delete_note(module, note, exact=True)
            elif not storage.exists(module):
                raise ModuleNotFound(f"Module '{module}' does not exist.")
            else:
                # Only an unsaved draft remains: its Markdown file is gone.
                storage.clear_note_draft(module, note)
            self.ctx.notes.discard(module, note)
        except Exception as e:
            # A failed cleanup keeps the draft tracked and usable.
            self.ctx.log.emit(f"! {e}")
            return
        self._current = None
        self.ctx.changed.emit()

    def _dirty(self) -> None:
        if self._buffer:
            b = self._buffer
            if b.recovery_error:
                text = "Recovery copy failed — save your note"
            elif not b.dirty:
                text = ""
            elif b.recovery_pending:
                text = "● unsaved · backing up…"
            else:
                text = "● unsaved · recovery copy saved"
                if b.recovered:
                    text = "Recovered draft · Save to keep changes"
            self.dirty.setText(text)
            self.dirty.setToolTip(b.recovery_error)

    def save(self) -> bool:
        if self._buffer is None:
            return True
        try:
            self._buffer.save()
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
            QMessageBox.warning(self, "Could not save note", str(e))
            return False
        self.dirty.setText("saved")
        self.ctx.changed.emit()
        return True


# --------------------------------------------------------------------------- #
class DocumentsComponent(BaseComponent):
    TITLE = "Documents"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.docs = QListWidget()
        self.docs.itemActivated.connect(lambda _i: self._open_selected())
        lay.addWidget(self.docs, 1)
        row = QHBoxLayout()
        add_btn = QPushButton("+ Add file…")
        add_btn.clicked.connect(self._browse)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(self._open_selected)
        row.addWidget(add_btn)
        row.addWidget(open_btn)
        lay.addLayout(row)
        self.hint = QLabel("Reference files (PDF/EPUB, etc.). Double-click to read in a pane.",
                           objectName="Muted")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        self.refresh()

    def focus_default(self) -> None:
        self.docs.setFocus()
        if self.docs.count() and self.docs.currentRow() < 0:
            self.docs.setCurrentRow(0)

    def _browse(self) -> None:
        if not self.app.current_module:
            self.ctx.log.emit("! Select or create a module first.")
            return
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add documents to this module", "",
            "Documents (*.pdf *.epub *.txt *.md *.docx *.mobi);;All files (*.*)")
        if not paths:
            return
        added = 0
        for p in paths:
            try:
                name = self.app.storage.add_document(self.app.current_module, p)
                self.ctx.log.emit(f"Added document '{name}' to {self.app.current_module}.")
                added += 1
            except Exception as e:
                self.ctx.log.emit(f"! {e}")
        if added:
            self.ctx.changed.emit()

    def _open_selected(self) -> None:
        item = self.docs.currentItem()
        if not item or not self.app.current_module:
            return
        name = item.text()
        if not item.flags() & Qt.ItemIsEnabled:
            return
        path = self.app.paths.documents_dir(self.app.current_module) / name
        if not path.exists():
            self.ctx.log.emit(f"! Missing file: {name}")
            return
        ext = path.suffix.lower()
        if ext in (".pdf", ".epub"):
            # open in an in-app viewer pane
            self.ctx.view_requested.emit(str(path))
        else:
            # unsupported in-app -> hand to the OS default app
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            self.ctx.log.emit(f"Opened '{name}' in your default app (no in-app viewer for {ext}).")

    def refresh(self) -> None:
        self.docs.clear()
        name = self.app.current_module
        if not name:
            return
        try:
            names = self.app.storage.document_names(name)
        except Exception as error:  # refresh runs on every change; never raise here
            item = QListWidgetItem(f"(could not list documents: {error})")
            item.setFlags(Qt.NoItemFlags)
            self.docs.addItem(item)
            return
        for d in names:
            self.docs.addItem(d)
        if self.docs.count() == 0:
            item = QListWidgetItem("(no documents yet — click + Add file)")
            item.setFlags(Qt.NoItemFlags)
            self.docs.addItem(item)


# --------------------------------------------------------------------------- #
class ConsoleComponent(BaseComponent):
    TITLE = "Console"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.out = QTextEdit(readOnly=True)
        lay.addWidget(self.out, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("vocab>", objectName="Muted"))
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("command (HELP, DOCTOR, STATS, DICT, NOTE, STUDY, ...)")
        self.cmd.returnPressed.connect(self._run)
        row.addWidget(self.cmd, 1)
        lay.addLayout(row)
        for text in ctx.messages:  # logged before this Console opened
            self.log(text)
        ctx.log.connect(self.log)

    def focus_default(self) -> None:
        self.cmd.setFocus()

    def log(self, text: str) -> None:
        from PySide6.QtGui import QTextCursor
        self.out.moveCursor(QTextCursor.End)
        self.out.insertPlainText(text + "\n")

    def _run(self) -> None:
        line = self.cmd.text().strip()
        if not line:
            return
        self.log(f"vocab> {line}")
        try:
            cmd = parse(line)
        except CommandSyntaxError as e:  # an unclosed quote: nothing runs
            self.log(f"! {e}")
            self.cmd.end(False)  # keep the line to correct, the cursor at its end
            return
        self.cmd.clear()
        if cmd is None:
            return
        if cmd.verb in ("STUDY", "STUDYALL") and cmd.body is None:
            all_mods = cmd.verb == "STUDYALL"
            if all_mods:
                self.ctx.study_requested.emit(None)
            else:
                target = " ".join(cmd.args) if cmd.args else self.app.current_module
                self.ctx.study_requested.emit([target] if target else [])
            return
        from . import backup_actions
        # Like the menu, warn that a backup holds the saved versions of notes
        # with unsaved changes. A command with a usage problem reports it.
        backs_up = ((cmd.verb == "BACKUP" and 1 <= len(cmd.args) <= 2)
                    or (cmd.verb == "BACKUPALL" and len(cmd.args) <= 1))
        if backs_up and cmd.body is None:
            modules = None  # BACKUPALL: every module
            if cmd.verb == "BACKUP":
                try:
                    modules = [self.app.storage.canonical_name(cmd.args[0])]
                except Exception:
                    modules = []  # the command itself reports the problem
            if modules != [] and not backup_actions.confirm_unsaved(self, self.ctx, modules):
                self.log("Cancelled; nothing was written.")
                return
        repl = Repl(self.app)
        repl._confirm = lambda text: QMessageBox.question(
            self, "Confirm", text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes
        # BACKUP, BACKUPALL and RESTORE report progress in a dialog with a
        # Cancel button, never on stdout.
        repl.progress = backup_actions.progress_callback(self)
        previous_module = self.app.current_module
        try:
            deleted = deleted_note = None
            if cmd.verb == "DELETE" and cmd.args:
                deleted = self.app.storage.canonical_name(" ".join(cmd.args))
                ask = repl._confirm

                def confirm_then_release(text):
                    # Close the module's readers only once the user said Yes.
                    if not ask(text):
                        return False
                    self.ctx.module_deleting.emit(deleted)
                    return True
                repl._confirm = confirm_then_release
            if (cmd.verb == "NOTE" and cmd.args and cmd.args[0].upper() in ("DEL", "DELETE", "RM")
                    and cmd.body is None and previous_module):
                # Resolve before deleting: on Windows "one" deletes One.md, and
                # the open buffer is keyed "One".
                try:
                    deleted_note = self.app.storage.canonical_note_name(
                        previous_module, " ".join(cmd.args[1:]))
                except Exception:
                    deleted_note = None  # the command itself reports the problem
            result = repl.dispatch(cmd)
            if result:
                self.log(result)
            if deleted:
                if not self.app.storage.exists(deleted):
                    self.ctx.notes.discard(deleted)
            # Only a note that is really gone loses its open buffer and draft;
            # a rejected command returns a message without deleting anything.
            if deleted_note and not self.app.storage._note_path(
                    previous_module, deleted_note, exact=True).exists():
                self.ctx.notes.discard(previous_module, deleted_note)
        except EOFError:
            self.log("(QUIT ignored in the app — just close the window)")
        except Exception as e:
            self.log(f"! {e}")
        finally:
            repl.progress.close()
        if previous_module != self.app.current_module:
            self.ctx.module_changed.emit(self.app.current_module or "")
        self.ctx.changed.emit()
