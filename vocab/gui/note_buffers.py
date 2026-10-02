"""Shared note documents: views can come and go without losing drafts."""
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QPlainTextDocumentLayout


class NoteBuffer(QObject):
    changed = Signal()

    def __init__(self, storage, module, note, parent):
        super().__init__(parent)
        self.storage, self.module, self.note = storage, module, note
        draft = storage.load_note_draft(module, note)
        self.baseline = draft["baseline"] if draft else storage.load_note(module, note)
        # A crash between saving Markdown and deleting the sidecar must not
        # reopen the already-saved text as an obsolete conflicting draft.
        if draft and storage._note_path(module, note).exists():
            saved = storage.load_note(module, note)
            if saved == draft["content"]:
                self.baseline = saved
        self.recovery_error = ""
        self.recovered = bool(draft and draft["content"] != self.baseline)
        self.recovery_pending = False
        self._active = True
        self.document = QTextDocument(self)
        self.document.setDocumentLayout(QPlainTextDocumentLayout(self.document))
        self.document.setPlainText(draft["content"] if draft else self.baseline)
        self.document.setModified(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.flush_recovery)
        self.document.contentsChanged.connect(self._edited)
        if draft and not self.dirty:
            self.flush_recovery()

    def _edited(self):
        if self._active:
            self.recovery_pending = True
            if not self._timer.isActive():
                self._timer.start()
            self.changed.emit()

    def flush_recovery(self):
        self._timer.stop()
        if not self._active:
            return
        try:
            if self.dirty:
                self.storage.save_note_draft(self.module, self.note, self.baseline,
                                             self.document.toPlainText())
            else:
                self.storage.clear_note_draft(self.module, self.note)
            self.recovery_error = ""
            self.recovery_pending = False
        except Exception as error:
            self.recovery_error = str(error)
        self.changed.emit()

    def clear_recovery(self):
        """Remove the recovery copy; raises if it cannot be removed."""
        self.storage.clear_note_draft(self.module, self.note)

    def deactivate(self):
        """Stop tracking this buffer's edits (its draft was discarded)."""
        self._timer.stop()
        self._active = False
        self.baseline = self.document.toPlainText()

    def discard(self):
        self.clear_recovery()
        self.deactivate()

    @property
    def dirty(self):
        return self.document.toPlainText() != self.baseline

    def reload_if_clean(self):
        if self.dirty:
            return
        content = self.storage.load_note(self.module, self.note)
        if content != self.baseline:
            self.baseline = content
            self.document.setPlainText(content)
            self.document.setModified(False)

    def save(self):
        content = self.document.toPlainText()
        # A file edited outside this app must not be silently overwritten.
        # Check and write under the write lock so nothing slips in between.
        with self.storage.transaction():
            path = self.storage._note_path(self.module, self.note)
            if path.exists() and self.storage.load_note(self.module, self.note) != self.baseline:
                raise ValueError(f"'{self.module}/{self.note}' changed on disk. "
                                 "Copy your draft before resolving the external change.")
            self.storage.save_note(self.module, self.note, content)
        self.baseline = content
        self.document.setModified(False)
        self.recovered = False
        self.flush_recovery()


class NoteBuffers(QObject):
    def __init__(self, storage, parent):
        super().__init__(parent)
        self.storage = storage
        self.buffers = {}
        self.recovery_errors = []
        for module in storage.list_names():
            try:
                drafts = storage.note_drafts(module)
            except Exception as error:
                self.recovery_errors.append(f"{module}: {error}")
                continue
            for note in drafts:
                try:
                    self.open(module, note)
                except Exception as error:
                    self.recovery_errors.append(str(error))

    def open(self, module, note):
        identity = (module, note)
        if identity not in self.buffers:
            self.buffers[identity] = NoteBuffer(self.storage, module, note, self)
        buffer = self.buffers[identity]
        buffer.reload_if_clean()
        return buffer

    def discard(self, module, note=None):
        self._discard([identity for identity in self.buffers
                       if identity[0] == module and (note is None or identity[1] == note)])

    def discard_buffers(self, buffers):
        self._discard([(buffer.module, buffer.note) for buffer in buffers])

    def _discard(self, identities):
        # All or nothing: deactivate buffers only once every recovery copy is
        # gone. Otherwise a cancelled close would leave an editable note whose
        # new edits were neither backed up nor offered at the next close.
        cleared, failures = [], []
        for identity in identities:
            buffer = self.buffers[identity]
            try:
                buffer.clear_recovery()
                cleared.append(buffer)
            except Exception as error:
                failures.append(f"{buffer.module}/{buffer.note}: {error}")
        if failures:
            for buffer in cleared:
                buffer.flush_recovery()  # restore the copies just removed
            raise OSError("Could not discard recovery copies. " + "; ".join(failures))
        for identity in identities:
            # Views detach on the next refresh; keep Qt ownership until then.
            self.buffers.pop(identity).deactivate()

    def dirty_buffers(self):
        return [buffer for buffer in self.buffers.values() if buffer.dirty]
