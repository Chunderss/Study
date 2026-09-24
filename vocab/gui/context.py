"""Shared context + signal bus for the workspace.

Components (Vocab, Notes, Documents, Console, Study) never call each other
directly. They talk through one WorkspaceContext: they read/write via ``app``
and emit ``changed`` when they mutate data, so every open pane can refresh. This
keeps components decoupled and independently testable.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool


class WorkspaceContext(QObject):
    # data mutated (word added, note saved, module created/deleted) -> refresh all
    changed = Signal()
    # the current module changed -> components should reload for the new module
    module_changed = Signal(str)
    # a component asks the workspace to open Study in a (possibly new) pane
    study_requested = Signal(object)   # payload: list[str] module names, or None=all
    # a component asks the workspace to open a document viewer in a new pane
    view_requested = Signal(str)       # payload: absolute file path
    # a reader highlighted a word to add to vocab: (word, sentence)
    add_word_requested = Signal(str, str, str)
    # a component wants to log a line to the console
    log = Signal(str)
    word_added = Signal(int, bool)

    def __init__(self, app):
        super().__init__()
        self.app = app
        from .note_buffers import NoteBuffers
        self.notes = NoteBuffers(app.storage, self)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._next_job = 0
        self._jobs = {}

    @property
    def busy(self):
        return bool(self._jobs)

    def add_word(self, word, manual=None, sentence="", target=None):
        module = target or self.app.current_module
        if not module:
            raise ValueError("Select or create a module first.")
        if not word.strip():
            raise ValueError("No word given.")
        if manual is not None:
            message = self.app.cmd_add(word, target=module, manual_def=manual)
            self.changed.emit()
            self.log.emit(message)
            return None
        self._next_job += 1
        token = self._next_job
        worker = LookupWorker(token, self.app, word, sentence)
        worker.signals.done.connect(self._lookup_done)
        self._jobs[token] = (worker, module, word)
        self.log.emit(f"Looking up '{word}'…")
        self._pool.start(worker)
        return token

    @Slot(int, object, str)
    def _lookup_done(self, token, result, error):
        worker, module, word = self._jobs.pop(token)
        ok = False
        try:
            if error:
                raise ValueError(error)
            senses, dictionary, notice = result
            message = self.app.add_resolved_word(word, module, senses, dictionary, notice)
            ok = True
            self.changed.emit()
        except Exception as exc:
            message = f"! Could not add '{word}': {exc}"
        self.log.emit(message)
        self.word_added.emit(token, ok)


class LookupSignals(QObject):
    done = Signal(int, object, str)


class LookupWorker(QRunnable):
    def __init__(self, token, app, word, sentence):
        super().__init__()
        self.token, self.app, self.word, self.sentence = token, app, word, sentence
        self.signals = LookupSignals()

    def run(self):
        try:
            result = self.app._lookup_senses(self.word, self.sentence)
        except Exception as exc:
            self.signals.done.emit(self.token, None, str(exc))
        else:
            self.signals.done.emit(self.token, result, "")
