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
        ctx.module_changed.connect(lambda _=None: self.refresh())
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
        self.add_input = QLineEdit()
        self.add_input.setPlaceholderText("Add a word (Enter).  word :: your definition  skips the dictionary")
        self.add_input.returnPressed.connect(self._add)
        row.addWidget(self.add_input, 1)
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        row.addWidget(add_btn)
        lay.addLayout(row)
        self.refresh()

    def focus_default(self) -> None:
        self.add_input.setFocus()

    def _add(self) -> None:
        raw = self.add_input.text().strip()
        if not raw:
            return
        if not self.app.current_module:
            self.ctx.log.emit("! Select or create a module first.")
            return
        word, manual = raw, None
        if "::" in raw:
            word, manual = [s.strip() for s in raw.split("::", 1)]
        try:
            self.ctx.log.emit(self.app.cmd_add(word, manual_def=manual))
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
        self.add_input.clear()
        self.ctx.changed.emit()

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
        self._loaded_text = ""
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
        if self.outer.orientation() != orientation:
            self.outer.setOrientation(orientation)
        if narrow:
            self._picker.setMaximumWidth(16777215)
            self._picker.setMaximumHeight(150)
            self.outer.setSizes([150, max(1, self.height() - 150)])
        else:
            self._picker.setMaximumHeight(16777215)
            self._picker.setMaximumWidth(200)
            self.outer.setSizes([170, max(1, self.width() - 170)])

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
        self.note_list.blockSignals(True)
        self.note_list.clear()
        name = self.app.current_module
        if name:
            for n in self.app.storage.note_names(name):
                self.note_list.addItem(n)
        self.note_list.blockSignals(False)
        # keep current selection if still present
        if self._current:
            items = self.note_list.findItems(self._current, Qt.MatchExactly)
            if items:
                self.note_list.setCurrentItem(items[0])
            else:
                self._current = None
                self.editor.clear(); self.title.setText("(no note)"); self._set_editing(False)
                self.markdown.render()

    def _select(self, note: str) -> None:
        if not note or not self.app.current_module:
            return
        identity = (self.app.current_module, note)
        if identity == self._loaded_note and self.editor.toPlainText() != self._loaded_text:
            return  # unrelated data refresh must not overwrite an unsaved draft
        try:
            content = self.app.storage.load_note(self.app.current_module, note)
        except Exception as e:
            self.ctx.log.emit(f"! {e}"); return
        self._current = note
        self._loaded_note = identity
        self._loaded_text = content
        self.editor.blockSignals(True)
        self.editor.setPlainText(content)
        self.editor.blockSignals(False)
        self.markdown.render()
        self.title.setText(note); self.dirty.setText(""); self._set_editing(True)

    def _new(self) -> None:
        if not self.app.current_module:
            self.ctx.log.emit("! Select or create a module first."); return
        name, ok = QInputDialog.getText(self, "New note", "Note name:")
        if not ok or not name.strip():
            return
        try:
            stem = self.app.storage.save_note(self.app.current_module, name.strip(), "")
        except Exception as e:
            self.ctx.log.emit(f"! {e}"); return
        self._current = stem
        self.ctx.changed.emit()
        self.editor.setFocus()

    def _delete(self) -> None:
        if not self._current or not self.app.current_module:
            return
        if QMessageBox.question(self, "Delete note", f"Delete '{self._current}'?") != QMessageBox.Yes:
            return
        try:
            self.app.storage.delete_note(self.app.current_module, self._current)
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
        self._current = None
        self.ctx.changed.emit()

    def _dirty(self) -> None:
        if self._current:
            self.dirty.setText("● unsaved")

    def save(self) -> None:
        if not self._current or not self.app.current_module:
            return
        try:
            self.app.storage.save_note(self.app.current_module, self._current,
                                       self.editor.toPlainText())
            self._loaded_text = self.editor.toPlainText()
            self.editor.document().setModified(False)
            self.dirty.setText("saved")
        except Exception as e:
            self.ctx.log.emit(f"! {e}")


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
        if name.startswith("("):   # placeholder row
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
            self.docs.addItem("(no documents yet — click + Add file)")


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
        self.out.append(text)

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
                self.ctx.study_requested.emit([target] if target else None)
            return
        repl = Repl(self.app)
        try:
            result = repl.dispatch(cmd)
            if result:
                self.log(result)
        except EOFError:
            self.log("(QUIT ignored in the app — just close the window)")
        except Exception as e:
            self.log(f"! {e}")
        self.ctx.changed.emit()
