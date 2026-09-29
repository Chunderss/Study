"""The real desktop entry point stays usable when module data breaks.

Each run starts vocab.gui's main() with real error dialogs. A timer closes any
modal dialog, records how many appeared and how deeply they nested, and ends
the run after a fixed series of focus changes.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import json, sys
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from vocab.cli.app import App
from vocab.gui import main_window

home, mode = Path(sys.argv[1]), sys.argv[2]
seen = {"dialogs": 0, "nesting": 0}

def close_dialogs():
    open_boxes = [w for w in QApplication.topLevelWidgets()
                  if isinstance(w, QMessageBox) and w.isVisible()]
    seen["nesting"] = max(seen["nesting"], len(open_boxes))
    modal = QApplication.activeModalWidget()
    if isinstance(modal, QMessageBox):
        seen["dialogs"] += 1
        modal.done(0)

closer = QTimer()
closer.setInterval(20)
closer.timeout.connect(close_dialogs)

init = main_window.MainWindow.__init__
def start_closer_first(self, *args, **kwargs):
    closer.start()  # startup errors open dialogs before the event loop runs
    init(self, *args, **kwargs)
main_window.MainWindow.__init__ = start_closer_first

def probe():
    win = next(w for w in QApplication.topLevelWidgets()
               if isinstance(w, main_window.MainWindow))
    def churn():
        for _ in range(3):
            for widget in (win.module_list, win.workspace.focused.component, win.module_list):
                widget.setFocus()
                QTest.qWait(30)
        win.ctx.changed.emit()
        QTest.qWait(300)
    churn()
    if mode == "failing-focus-slot":
        def fail(*args):
            raise RuntimeError("focus slot failed")
        QApplication.instance().focusChanged.connect(fail)
        churn()
    elif mode == "deleted-at-runtime":
        App(home).cmd_delete("Book")
        churn()
    elif mode == "damaged-at-runtime":
        (home / "modules" / "Book" / "vocab" / "stats.json").write_text("{", encoding="utf-8")
        churn()
    print("RESULT " + json.dumps({**seen, "visible": win.isVisible(),
                                  "status": win.status.text()}), flush=True)
    QApplication.instance().quit()

exec_ = QApplication.exec
def exec_with_probe(*args):
    QTimer.singleShot(200, probe)
    return exec_()
QApplication.exec = exec_with_probe

from vocab.gui.__main__ import main
sys.argv = ["VocabStudy", "--home", str(home)]
main()
'''


def prepare(home, damaged):
    from vocab.cli.app import App
    app = App(home)
    app.cmd_create("Book")
    app.cmd_add("word", target="Book", manual_def="a unit of language")
    (home / "workspace.json").write_text(json.dumps({
        "schema": 1, "module": "Book", "sidebar": True,
        "workspace": {"focused": 0, "zoomed": False, "tree": {
            "direction": "h", "sizes": [500, 500], "children": [
                {"order": 0, "pane": {"component": "vocab"}},
                {"order": 1, "pane": {"component": "notes"}}]}}}), encoding="utf-8")
    if damaged:
        app.paths.stats_file("Book").write_text("{corrupt", encoding="utf-8")


@pytest.mark.parametrize("mode", ["damaged-at-start", "deleted-at-runtime", "damaged-at-runtime"])
def test_damaged_or_removed_module_opens_no_error_dialogs(tmp_path, mode):
    prepare(tmp_path, damaged=mode == "damaged-at-start")
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run([sys.executable, "-c", SCRIPT, str(tmp_path), mode], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    line = next(l for l in result.stdout.splitlines() if l.startswith("RESULT "))
    seen = json.loads(line[len("RESULT "):])
    assert seen["dialogs"] == 0 and seen["nesting"] == 0, seen
    assert seen["visible"], seen
    if mode == "deleted-at-runtime":
        # The vanished module is deselected rather than kept as a broken selection.
        assert seen["status"].startswith("module: —"), seen
    else:
        assert "due: ?" in seen["status"], seen
        assert "unreadable module data" in seen["status"], seen


def test_repeated_slot_errors_show_one_dialog_at_a_time(tmp_path):
    # A slot that fails on every focus change: its own dialog moves focus.
    prepare(tmp_path, damaged=False)
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run([sys.executable, "-c", SCRIPT, str(tmp_path), "failing-focus-slot"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next(l for l in result.stdout.splitlines() if l.startswith("RESULT "))
    seen = json.loads(line[len("RESULT "):])
    # Identical errors repeated within seconds are logged, not shown again.
    assert seen["dialogs"] == 1 and seen["nesting"] == 1, seen
    assert "focus slot failed" in (tmp_path / "desktop.log").read_text(encoding="utf-8")
