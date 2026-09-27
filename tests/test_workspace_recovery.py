"""Workspace splits and failed persistence must leave a restorable pane tree."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from .test_gui_reliability import window
from .test_learning_gui import make_pdf
from .test_markdown_preview import qt
from vocab.gui.components import VocabComponent
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import PdfViewer
from vocab.gui.workspace import Workspace


@pytest.mark.parametrize("split, orientation", [
    ("split_vertical", Qt.Horizontal),
    ("split_horizontal", Qt.Vertical),
])
def test_reader_split_saves_and_restores_workspace(window, qt, monkeypatch, split, orientation):
    viewer = window._open_viewer(str(make_pdf(window)))
    viewer.go_to({"page": 1})
    getattr(window.workspace, split)()
    assert window.workspace.focused.factory_key == "vocab"
    assert isinstance(window.workspace.focused.component, VocabComponent)

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    assert window.close()
    assert not warnings
    reopened = MainWindow(root=window.app.paths.root)
    try:
        assert not reopened._session_warning
        panes = reopened.workspace._panes
        assert len(panes) == 2
        assert isinstance(panes[0].component, PdfViewer)
        assert panes[0].component._view.pageNavigator().currentPage() == 1
        assert panes[0].parentWidget().orientation() == orientation
        assert isinstance(panes[1].component, VocabComponent)
        assert reopened.workspace.focused is panes[1]
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()


def test_splitting_study_uses_a_restorable_component(window):
    window.app.cmd_add("word", manual_def="definition")
    window._open_study(["Book"])
    assert window.workspace.focused.factory_key == "study"
    window.workspace.split_vertical()
    assert window.workspace.focused.factory_key == "vocab"
    assert isinstance(window.workspace.focused.component, VocabComponent)
    window._save_session()


@pytest.mark.parametrize("zoomed", [False, True])
@pytest.mark.parametrize("has_context", [False, True])
def test_failed_close_preserves_workspace_and_can_be_retried(qt, monkeypatch, zoomed, has_context):
    messages = []

    class Reader(QWidget):
        def __init__(self):
            super().__init__()
            self.ctx = SimpleNamespace(log=SimpleNamespace(emit=messages.append)) if has_context else None

        def on_replaced(self):
            raise OSError("Disk full")

    workspace = Workspace(lambda key: Reader(), ["reader"])
    workspace.split_horizontal()
    if zoomed:
        workspace.toggle_zoom()
    pane = workspace.focused
    original_parent = pane.parentWidget()
    describe = lambda pane: {"component": pane.factory_key}
    original_state = workspace.snapshot(describe)
    try:
        if has_context:
            workspace.close_focused()
            assert messages == ["! Could not close pane: Disk full"]
        else:
            with pytest.raises(OSError, match="Disk full"):
                workspace.close_focused()
        assert workspace.focused is pane
        assert pane.parentWidget() is original_parent
        assert workspace.snapshot(describe) == original_state

        monkeypatch.setattr(pane.component, "on_replaced", lambda: None)
        workspace.close_focused()
        assert len(workspace._panes) == 1
        assert workspace.focused is workspace._panes[0]
        assert pane.parentWidget() is None
        workspace.snapshot(describe)
    finally:
        workspace.deleteLater()
        qt.processEvents()
