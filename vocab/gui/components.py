"""Workspace components — the things that live inside panes.

Each component is a QWidget with:
  * TITLE            : str shown in the pane title bar
  * refresh()        : reload from the current module
  * focus_default()  : put keyboard focus on the primary control

Components talk only to WorkspaceContext (ctx). No component references another.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QSplitter, QTextEdit, QVBoxLayout,
                               QWidget)

from ..cli.parser import parse
from ..cli.repl import Repl
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
class VocabComponent(BaseComponent):
    TITLE = "Vocab"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.words = QTextEdit(readOnly=True)
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

    def refresh(self) -> None:
        name = self.app.current_module
        if not name:
            self.words.setPlainText("No module selected.")
            return
        wl = self.app.storage.load_words(name)
        if not wl.words:
            self.words.setPlainText("(no vocab yet) — add your first word below.")
            return
        lines = []
        for key in sorted(wl.words):
            w = wl.words[key]
            pos = f"[{w.senses[0].pos}] " if w.senses and w.senses[0].pos else ""
            lines.append(f"{w.word}\n    {pos}{w.primary_definition()}")
        self.words.setPlainText("\n".join(lines))


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
        try:
            self.app.storage.delete_note(module, note)
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
            return
        self.ctx.notes.discard(module, note)
        self._current = None
        self.ctx.changed.emit()

    def _dirty(self) -> None:
        if self._buffer:
            self.dirty.setText("● unsaved" if self._buffer.dirty else "")

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
        for d in self.app.storage.document_names(name):
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
        self.cmd.clear()
        self.log(f"vocab> {line}")
        cmd = parse(line)
        if cmd is None:
            return
        if cmd.verb in ("STUDY", "STUDYALL"):
            all_mods = cmd.verb == "STUDYALL"
            if all_mods:
                self.ctx.study_requested.emit(None)
            else:
                target = " ".join(cmd.args) if cmd.args else self.app.current_module
                self.ctx.study_requested.emit([target] if target else [])
            return
        repl = Repl(self.app)
        repl._confirm = lambda text: QMessageBox.question(
            self, "Confirm", text, QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes
        previous_module = self.app.current_module
        try:
            result = repl.dispatch(cmd)
            if result:
                self.log(result)
            if cmd.verb == "DELETE" and cmd.args:
                deleted = " ".join(cmd.args)
                if not self.app.storage.exists(deleted):
                    self.ctx.notes.discard(deleted)
            if cmd.verb == "NOTE" and cmd.args and cmd.args[0].upper() in ("DEL", "DELETE", "RM"):
                from ..core.storage import _sanitize_note_name
                note = _sanitize_note_name(" ".join(cmd.args[1:]))
                self.ctx.notes.discard(previous_module, note)
        except EOFError:
            self.log("(QUIT ignored in the app — just close the window)")
        except Exception as e:
            self.log(f"! {e}")
        if previous_module != self.app.current_module:
            self.ctx.module_changed.emit(self.app.current_module or "")
        self.ctx.changed.emit()
