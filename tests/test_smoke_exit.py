"""The desktop --smoke-test must fail when any check or slot fails."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

# Runs the real entry point with an offline stand-in for the WordNet corpus.
SCRIPT = r'''
import sys
from vocab.core.models import Sense
from vocab.dictionaries.wordnet import WordNet
WordNet.lookup = lambda self, word: [Sense("a procedure for evaluation")]
if sys.argv[2] == "slot-error":
    from vocab.gui.main_window import MainWindow
    show = MainWindow._show_message
    def failing(self, text):
        show(self, text)
        # Emitted through ctx.log by the smoke's learning review, after which
        # the remaining smoke checks still pass.
        if text.startswith("Explanation saved"):
            raise RuntimeError("injected slot failure")
    MainWindow._show_message = failing
elif sys.argv[2] == "book-script-runs":
    # Simulates losing the EPUB script protection in a build.
    from PySide6.QtWebEngineCore import QWebEngineSettings
    from vocab.gui.viewers import EpubViewer
    init = EpubViewer.__init__
    def scripts_enabled(self, *args, **kwargs):
        init(self, *args, **kwargs)
        self._web.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        self._load(0)
    EpubViewer.__init__ = scripts_enabled
from vocab.gui.__main__ import main
sys.argv = ["VocabStudy", "--home", sys.argv[1], "--smoke-test"]
main()
'''


def run_smoke(home, mode):
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"  # the smoke opens a real EPUB reader
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    return subprocess.run([sys.executable, "-c", SCRIPT, str(home), mode], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("mode, code", [("clean", 0), ("slot-error", 1), ("book-script-runs", 1)])
def test_smoke_exit_status_reflects_failures(tmp_path, mode, code):
    result = run_smoke(tmp_path, mode)
    assert result.returncode == code, result.stdout + result.stderr
    # Every run reaches the learning review; a clean run also opens both documents.
    assert (tmp_path / "modules" / "DesktopCheck" / "learning.json").is_file()
    if mode == "clean":
        documents = tmp_path / "modules" / "DesktopCheck" / "documents"
        assert (documents / "check.epub").is_file() and (documents / "check.pdf").is_file()
    log = tmp_path / "desktop.log"
    if mode == "slot-error":
        assert "injected slot failure" in result.stderr
        assert "injected slot failure" in log.read_text(encoding="utf-8")
    elif mode == "book-script-runs":
        assert "Offline reader ready|script ran" in result.stderr
    else:
        assert not log.exists() or not log.read_text(encoding="utf-8").strip()
