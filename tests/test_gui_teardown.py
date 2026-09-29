"""A failing GUI test must fail promptly, not hang in fixture teardown."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import threading
from tests.test_gui_reliability import qt, window


def start_lookup(window, monkeypatch, release):
    from vocab.core.models import Sense
    def lookup(*args):
        release.wait(5)
        return [Sense("definition")], "test", ""
    monkeypatch.setattr(window.app, "_lookup_senses", lookup)
    vc = window.workspace.focused.component
    vc.add_input.setText("word")
    vc._add()
    assert window.ctx.busy


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
    assert "2 failed" in output, output
    assert "INJECTED-WHILE-BLOCKED" in output and "INJECTED-BEFORE-COMPLETION" in output, output
