"""Shared context + signal bus for the workspace.

Components (Vocab, Notes, Documents, Console, Study) never call each other
directly. They talk through one WorkspaceContext: they read/write via ``app``
and emit ``changed`` when they mutate data, so every open pane can refresh. This
keeps components decoupled and independently testable.
"""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool


# Recent log lines, for a Console opened later. Kept in memory only.
MESSAGE_HISTORY = 200


class WorkspaceContext(QObject):
    # data mutated (word added, note saved, module created/deleted) -> refresh all
    changed = Signal()
    # the current module changed -> components should reload for the new module
    module_changed = Signal(str)
    # a component asks the workspace to open Study in a (possibly new) pane
    study_requested = Signal(object)   # payload: list[str] module names, or None=all
    # a component asks the workspace to open a document viewer in a new pane
    view_requested = Signal(str)       # payload: absolute file path
    # a reader highlighted a word to add to vocab: (word, sentence, module)
    add_word_requested = Signal(str, str, str)
    # a component wants to log a line to the console
    log = Signal(str)
    word_added = Signal(int, bool)
    learning_changed = Signal()
    capture_requested = Signal(str, str, object)  # module, excerpt, source location
    source_requested = Signal(str, object)
    # a module is about to be deleted: close anything holding its files open
    module_deleting = Signal(str)
    # a lookup finished for (module, word); added is False when an existing
    # entry was kept. The sense picker offers the senses of an added word.
    senses_available = Signal(str, str, bool)
    # find_more_senses(listing) finished: (token, listing with its extra senses)
    more_senses_done = Signal(int, object)

    def __init__(self, app):
        super().__init__()
        self.app = app
        # Connected first, so a line is recorded before other slots run.
        self.messages = deque(maxlen=MESSAGE_HISTORY)
        self.log.connect(self.messages.append)
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
        # Snapshot now; the commit is refused if the module or word changes
        # (another pane, another process, delete and recreate) meanwhile.
        expected = self.app.word_state(module, word)
        self._next_job += 1
        token = self._next_job
        worker = LookupWorker(token, self.app, word, sentence)
        worker.signals.done.connect(self._lookup_done)
        self._jobs[token] = (worker, module, word, expected)
        self.log.emit(f"Looking up '{word}'…")
        self._pool.start(worker)
        return token

    @Slot(int, object, str)
    def _lookup_done(self, token, result, error):
        worker, module, word, expected = self._jobs.pop(token)
        ok, committed = False, None
        try:
            if error:
                raise ValueError(error)
            senses, dictionary, notice = result
            committed = self.app.commit_resolved_word(word, module, senses, dictionary, notice,
                                                      expected=expected, sentence=worker.sentence)
            message = committed.message
            if committed.added and len(senses) > 1:
                message += "\n    Ctrl+B w lists them all."
            ok = True
            self.changed.emit()
        except Exception as exc:
            message = f"! Could not add '{word}': {exc}"
        self.log.emit(message)
        self.word_added.emit(token, ok)
        if committed is not None:
            self.senses_available.emit(committed.module, committed.word, committed.added)

    def find_more_senses(self, listing):
        """Look up the senses of a listed word's dictionary that are not saved,
        on the lookup thread (so closing waits for it). more_senses_done
        reports the result with the returned token."""
        self._next_job += 1
        token = self._next_job
        worker = MoreSensesWorker(token, self.app, listing)
        worker.signals.done.connect(self._more_senses_done)
        self._jobs[token] = worker
        self._pool.start(worker)
        return token

    @Slot(int, object)
    def _more_senses_done(self, token, listing):
        self._jobs.pop(token, None)
        self.more_senses_done.emit(token, listing)


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


class MoreSensesSignals(QObject):
    done = Signal(int, object)


class MoreSensesWorker(QRunnable):
    def __init__(self, token, app, listing):
        super().__init__()
        self.token, self.app, self.listing = token, app, listing
        self.signals = MoreSensesSignals()

    def run(self):
        try:
            listing = self.app.more_senses(self.listing)
        except Exception as exc:  # more_senses reports failures itself
            listing = self.listing.with_extra(note=str(exc))
        self.signals.done.emit(self.token, listing)
