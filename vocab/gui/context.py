"""Shared context + signal bus for the workspace.

Components (Vocab, Notes, Documents, Console, Study) never call each other
directly. They talk through one WorkspaceContext: they read/write via ``app``
and emit ``changed`` when they mutate data, so every open pane can refresh. This
keeps components decoupled and independently testable.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


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
    add_word_requested = Signal(str, str)
    # a component wants to log a line to the console
    log = Signal(str)

    def __init__(self, app):
        super().__init__()
        self.app = app
