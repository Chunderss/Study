"""Shared note documents: views can come and go without losing drafts."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QPlainTextDocumentLayout


class NoteBuffer(QObject):
    changed = Signal()

    def __init__(self, storage, module, note, parent):
        super().__init__(parent)
        self.storage, self.module, self.note = storage, module, note
        self.baseline = storage.load_note(module, note)
        self.document = QTextDocument(self)
        self.document.setDocumentLayout(QPlainTextDocumentLayout(self.document))
        self.document.setPlainText(self.baseline)
        self.document.setModified(False)
        self.document.contentsChanged.connect(self.changed)

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
        path = self.storage._note_path(self.module, self.note)
        if path.exists() and self.storage.load_note(self.module, self.note) != self.baseline:
            raise ValueError(f"'{self.module}/{self.note}' changed on disk. "
                             "Copy your draft before resolving the external change.")
        self.storage.save_note(self.module, self.note, content)
        self.baseline = content
        self.document.setModified(False)
        self.changed.emit()


class NoteBuffers(QObject):
    def __init__(self, storage, parent):
        super().__init__(parent)
        self.storage = storage
        self.buffers = {}

    def open(self, module, note):
        identity = (module, note)
        if identity not in self.buffers:
            self.buffers[identity] = NoteBuffer(self.storage, module, note, self)
        buffer = self.buffers[identity]
        buffer.reload_if_clean()
        return buffer

    def discard(self, module, note=None):
        for identity in list(self.buffers):
            if identity[0] == module and (note is None or identity[1] == note):
                buffer = self.buffers.pop(identity)
                # Views detach on the next refresh; keep Qt ownership until then.
                buffer.baseline = buffer.document.toPlainText()

    def dirty_buffers(self):
        return [buffer for buffer in self.buffers.values() if buffer.dirty]
