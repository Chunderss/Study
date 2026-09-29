"""The installed Qt binding must report errors raised in Qt slots.

The desktop app relies on sys.excepthook for errors inside signal handlers.
PySide6 6.8.0 and 6.8.0.1 instead freeze the whole event loop when a slot
raises, so pyproject requires 6.8.0.2 or later.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

SCRIPT = r'''
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
app = QApplication([])
reported, ticks = [], []
sys.excepthook = lambda kind, error, tb: reported.append(error)
def fail():
    raise RuntimeError("slot failed")
QTimer.singleShot(10, fail)
beat = QTimer()
beat.timeout.connect(lambda: ticks.append(1))
beat.start(10)
QTimer.singleShot(300, app.quit)
app.exec()
print("REPORTED", len(reported), "TICKS", len(ticks), flush=True)
'''


def test_slot_exceptions_reach_excepthook_without_freezing(tmp_path):
    import PySide6
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        result = subprocess.run([sys.executable, "-c", SCRIPT], cwd=tmp_path, env=env,
                                capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        pytest.fail(f"PySide6 {PySide6.__version__} froze after a slot raised; "
                    "install PySide6 6.8.0.2 or later.")
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(l for l in result.stdout.splitlines() if l.startswith("REPORTED"))
    _, reported, _, ticks = line.split()
    assert int(reported) == 1 and int(ticks) > 5, line
