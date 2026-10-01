"""The desktop --smoke-test must fail when any check or slot fails."""
import os
from pathlib import Path
import subprocess
import sys
import time

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
elif sys.argv[2] == "qt-fatal":
    # Qt aborts on fatal messages (e.g. a bundle missing QtWebEngineProcess).
    from PySide6.QtCore import qFatal
    from vocab.gui.main_window import MainWindow
    show = MainWindow._show_message
    def fatal(self, text):
        show(self, text)
        if text.startswith("Explanation saved"):
            qFatal("injected qt fatal")
    MainWindow._show_message = fatal
elif sys.argv[2] == "reader-error":
    from vocab.gui.viewers import EpubViewer
    def unreadable(self, *args, **kwargs):
        raise OSError("injected chapter failure")
    EpubViewer._chapter_url = unreadable
elif sys.argv[2] == "error-with-result":
    # A reader error arrives together with the expected script result.
    from vocab.gui.viewers import EpubViewer
    run_js = EpubViewer._run_js
    def error_then_result(self, script, callback):
        def deliver(value):
            if "getElementById('check')" in script:
                self.ctx.log.emit("! injected reader error")
            callback(value)
        run_js(self, script, deliver)
    EpubViewer._run_js = error_then_result
elif sys.argv[2] == "label-with-result":
    # The reader shows its own error label as the script result arrives.
    from vocab.gui.viewers import EpubViewer
    run_js = EpubViewer._run_js
    def label_then_result(self, script, callback):
        def deliver(value):
            if "getElementById('check')" in script:
                self._show_error("injected label error")
            callback(value)
        run_js(self, script, deliver)
    EpubViewer._run_js = label_then_result
elif sys.argv[2] == "late-error":
    # An error reported after the document checks, while the smoke finishes.
    from vocab.gui import learning
    init = learning.LearningComponent.__init__
    def late(self, ctx, *args, **kwargs):
        init(self, ctx, *args, **kwargs)
        ctx.log.emit("! injected late error")
    learning.LearningComponent.__init__ = late
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


@pytest.mark.parametrize("mode, code", [("clean", 0), ("slot-error", 1), ("book-script-runs", 1),
                                        ("reader-error", 1), ("qt-fatal", None),
                                        ("error-with-result", 1), ("label-with-result", 1),
                                        ("late-error", 1)])
def test_smoke_exit_status_reflects_failures(tmp_path, mode, code):
    started = time.monotonic()
    result = run_smoke(tmp_path, mode)
    if code is None:
        assert result.returncode != 0, result.stdout + result.stderr  # aborted
    else:
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
    elif mode == "reader-error":
        # The reader's own error ends the wait early and names the cause.
        assert "EPUB chapter load failed" in result.stderr
        assert "injected chapter failure" in result.stderr
        assert time.monotonic() - started < 25
    elif mode in ("error-with-result", "label-with-result"):
        # The error outranks the valid result: the script wait itself fails.
        injected = "injected reader error" if mode == "error-with-result" else "injected label error"
        assert injected in log.read_text(encoding="utf-8")
        assert "EPUB reader script failed: " in result.stderr and injected in result.stderr
    elif mode == "late-error":
        # Reported after the document checks finished, yet still fails the run.
        assert "injected late error" in log.read_text(encoding="utf-8")
        assert "Smoke check: ! injected late error" in result.stderr
        assert "failed:" not in result.stderr and "Document checks reported" not in result.stderr
    elif mode == "qt-fatal":
        # PySide words a qFatal() made from Python itself; Qt's own keep their text.
        assert "ERROR Qt QtFatalMsg:" in log.read_text(encoding="utf-8")
    else:
        assert not log.exists() or not log.read_text(encoding="utf-8").strip()
