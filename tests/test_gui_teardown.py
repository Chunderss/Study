"""A failing GUI test must fail promptly, not hang in fixture teardown."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import threading
from PySide6.QtTest import QTest
from shiboken6 import isValid
from tests.test_gui_reliability import qt, window

WINDOWS = []


def start_lookup(window, monkeypatch, release, wait=5):
    from vocab.core.models import Sense
    def lookup(*args):
        release.wait(wait)
        return [Sense("definition")], "test", ""
    monkeypatch.setattr(window.app, "_lookup_senses", lookup)
    vc = window.workspace.focused.component
    vc.add_input.setText("word")
    vc._add()
    assert window.ctx.busy
    WINDOWS.append(window)


def test_fails_while_worker_is_blocked(window, monkeypatch):
    start_lookup(window, monkeypatch, threading.Event())
    assert False, "INJECTED-WHILE-BLOCKED"


def test_fails_before_completion_is_delivered(window, monkeypatch):
    release = threading.Event()
    release.set()
    start_lookup(window, monkeypatch, release)
    # The worker finished, but its queued completion has not been processed.
    assert window.ctx._pool.waitForDone(5000)
    assert window.ctx.busy
    assert False, "INJECTED-BEFORE-COMPLETION"


def test_teardown_closed_those_windows_and_drained_their_lookups():
    assert len(WINDOWS) == 2
    for win in WINDOWS:
        assert not win.ctx._jobs, "a lookup was still registered after teardown"
        assert not isValid(win) or not win.isVisible(), "teardown left a window open"


LONG = threading.Event()


def test_worker_outliving_the_drain_is_reported(window, monkeypatch):
    # Passes itself; its teardown must fail explicitly instead of hanging.
    start_lookup(window, monkeypatch, LONG, wait=12)
    # An unsaved note makes closing this window ask Save/Discard later.
    window.ctx.notes.open("Book", "One").document.setPlainText("unsaved draft")


SEEN = []
WATCH = []


def dismiss_real_dialogs():
    from PySide6.QtWidgets import QApplication, QMessageBox
    for widget in QApplication.topLevelWidgets():
        if isinstance(widget, QMessageBox) and widget.isVisible():
            SEEN.append(widget.windowTitle())
            widget.done(QMessageBox.Discard)


def test_outlived_window_stays_out_of_the_way(window):
    outlived = WINDOWS[2]
    assert isValid(outlived) and outlived.ctx.busy and not outlived.isVisible()
    assert not outlived.hotkeys._app
    window.workspace.focused.component.focus_default()
    assert window.status.text().startswith("module: Book")
    LONG.set()
    for _ in range(300):
        if not outlived.ctx.busy:
            break
        QTest.qWait(10)
    assert not outlived.ctx.busy
    assert outlived.app.storage.load_words("Book").has("word")
    # Closing it later must also cope with a close-time warning dialog.
    def fail_save():
        raise OSError("injected layout save failure")
    outlived._save_session = fail_save
    # Record (and dismiss) any real dialog from here on instead of hanging.
    from PySide6.QtCore import QTimer
    WATCH.append(QTimer())
    WATCH[0].timeout.connect(dismiss_real_dialogs)
    WATCH[0].start(20)


def test_next_fixture_closes_the_finished_window_without_dialogs(window, qt):
    from PySide6.QtCore import Qt
    from tests.test_gui_reliability import _OUTLIVED
    outlived = WINDOWS[2]
    assert SEEN == [], f"real dialogs appeared during cleanup: {SEEN}"
    assert outlived not in _OUTLIVED
    assert not isValid(outlived) or not outlived.isVisible()
    vc = window.workspace.focused.component
    vc.add_input.setFocus()
    qt.processEvents()
    QTest.keyClick(vc.add_input, Qt.Key_B, Qt.ControlModifier)
    QTest.keyClick(vc.add_input, Qt.Key_V)
    assert len(window.workspace._panes) == 2
'''


def test_failed_lookup_tests_report_failure_instead_of_hanging(tmp_path):
    probe = tmp_path / "test_probe_teardown.py"
    probe.write_text(PROBE, encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH")]))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(probe)],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    # The two injected failures, one explicit teardown error for the worker that
    # outlived the drain, and the checks that cleanup was complete and contained.
    assert re.search(r"^2 failed, 4 passed, 1 error in ", output, re.M), output
    assert "still running after the 5 s teardown drain" in output, output
    assert "Signal source has been deleted" not in output, output
    assert "INJECTED-WHILE-BLOCKED" in output and "INJECTED-BEFORE-COMPLETION" in output, output
