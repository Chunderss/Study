"""Late renderer callbacks must not consume another navigation's scroll target."""
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QWidget
from shiboken6 import delete, isValid

from .test_markdown_preview import qt
from vocab.gui.viewers import EpubViewer


@pytest.fixture
def restore(qt, tmp_path):
    class DeferredPage:
        def runJavaScript(self, script, callback):
            self.complete = callback

    # Keep Qt lifetime checks real while controlling renderer callback delivery.
    viewer = EpubViewer.__new__(EpubViewer)
    QWidget.__init__(viewer)
    page = DeferredPage()
    viewer._web = SimpleNamespace(page=lambda: page, isVisible=lambda: True)
    viewer._requested_url = QUrl.fromLocalFile(str(tmp_path / "chapter.html"))
    viewer._load_generation = 1
    viewer._pending_scroll = 0.25
    yield viewer, page
    if isValid(viewer):
        delete(viewer)


def test_current_exhausted_restore_releases_pending_scroll(restore):
    viewer, page = restore
    viewer._restore_scroll(1, attempt=viewer.MAX_RESTORE_RETRIES)
    assert viewer._pending_scroll == 0.25
    page.complete(False)
    assert viewer._pending_scroll == 0.0


def test_hidden_reader_preserves_pending_without_running_javascript(restore, monkeypatch):
    viewer, page = restore
    scripts = []
    monkeypatch.setattr(page, "runJavaScript", lambda *args: scripts.append(args))
    viewer._web.isVisible = lambda: False
    viewer._restore_scroll(1)
    assert viewer._pending_scroll == 0.25
    assert scripts == []


@pytest.mark.parametrize("applied", [False, True])
def test_reader_hidden_during_callback_preserves_pending_without_retry(restore, monkeypatch, applied):
    viewer, page = restore
    scheduled = []
    monkeypatch.setattr("vocab.gui.viewers.QTimer.singleShot", lambda *args: scheduled.append(args))
    viewer._restore_scroll(1, attempt=viewer.MAX_RESTORE_RETRIES)
    viewer._web.isVisible = lambda: False
    page.complete(applied)
    assert viewer._pending_scroll == 0.25
    assert scheduled == []


@pytest.mark.parametrize("changed", ["generation", "fraction"])
@pytest.mark.parametrize("attempt, applied", [
    (EpubViewer.MAX_RESTORE_RETRIES, False),
    (0, True),
    (0, False),
])
def test_old_callback_preserves_new_pending_scroll_without_retry(restore, monkeypatch, changed, attempt, applied):
    viewer, page = restore
    scheduled = []
    monkeypatch.setattr("vocab.gui.viewers.QTimer.singleShot", lambda *args: scheduled.append(args))
    viewer._restore_scroll(1, attempt=attempt)
    if changed == "generation":
        viewer._load_generation = 2
    else:
        viewer._pending_scroll = 0.75
    pending = viewer._pending_scroll
    page.complete(applied)
    assert viewer._pending_scroll == pending
    assert scheduled == []


@pytest.mark.parametrize("applied", [False, True])
def test_callback_after_widget_deletion_neither_retries_nor_mutates(restore, applied):
    viewer, page = restore
    viewer._restore_scroll(1)
    delete(viewer)
    page.complete(applied)
    assert viewer._pending_scroll == 0.25
