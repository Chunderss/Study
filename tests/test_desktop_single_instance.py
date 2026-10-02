"""Only one desktop may own a data folder and its note recovery copies."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

from vocab.cli.app import App
from vocab.core.locking import DataFolderBusy, DesktopInstanceLock

ROOT = Path(__file__).resolve().parents[1]

HOLDER = r"""
import sys, time
from vocab.core.locking import DesktopInstanceLock
lock = DesktopInstanceLock(sys.argv[1])
print("HELD", flush=True)
if sys.argv[2] == "crash":
    import os
    os._exit(0)
time.sleep(float(sys.argv[2]))
"""

# The real desktop entry point; the "already open" notice is recorded, not shown.
DESKTOP = r"""
import sys
from PySide6.QtWidgets import QMessageBox
def notice(parent, title, text, *args):
    print("NOTICE", title, "|", text.replace("\n", " "), flush=True)
QMessageBox.information = staticmethod(notice)
from vocab.gui.__main__ import main
sys.argv = ["VocabStudy", "--home", sys.argv[1]]
main()
"""


def holder(home, mode="20"):
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(home), mode], cwd=ROOT,
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "HELD"
    return process


def start_desktop(home):
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.run([sys.executable, "-c", DESKTOP, str(home)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_second_desktop_exits_without_touching_recovery_copies(tmp_path):
    home = tmp_path / "home"
    app = App(home)
    app.cmd_create("Book")
    app.storage.save_note("Book", "One", "saved")
    app.storage.save_note_draft("Book", "One", "saved", "live draft of the first window")
    draft = app.storage._draft_path("Book", "One")
    before = draft.read_bytes()
    first = holder(home)
    try:
        result = start_desktop(home)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "NOTICE Vocab Study is already open" in result.stdout
        assert str(home) in result.stdout
        assert draft.read_bytes() == before
        # Through another spelling of the same folder, too.
        alias = tmp_path / "alias"
        alias.symlink_to(home, target_is_directory=True)
        with pytest.raises(DataFolderBusy):
            DesktopInstanceLock(alias)
        # The CLI keeps working beside the desktop.
        assert App(home).cmd_add("word", target="Book", manual_def="meaning").startswith("Added")
    finally:
        first.kill()
        first.wait(10)


def test_other_folders_and_crashed_desktops_do_not_block(tmp_path):
    first = holder(tmp_path / "one")
    try:
        DesktopInstanceLock(tmp_path / "two").release()
    finally:
        first.kill()
        first.wait(10)
    crashed = holder(tmp_path / "three", "crash")
    crashed.wait(10)
    DesktopInstanceLock(tmp_path / "three").release()
