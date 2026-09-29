"""Restore an EPUB behind a zoomed pane without losing its reading position."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")


@pytest.mark.parametrize("action", ["hidden", "scroll", "top"])
def test_restored_hidden_epub_preserves_position_and_accepts_later_scroll(tmp_path, action):
    script = r'''
import faulthandler
faulthandler.enable()
import json
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.cli.app import App
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import EpubViewer

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
root, action = Path(sys.argv[1]), sys.argv[2]
data = App(root)
data.cmd_create("Book")
data.cmd_use("Book")
path = data.paths.documents_dir("Book") / "book.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="one.html"/></manifest><spine><itemref idref="one"/></spine></package>')
    book.writestr("one.html", '<html><head><title>Book</title></head><body>' + '<p>Paragraph for reading.</p>' * 400 + '</body></html>')
data.storage.save_reading_pos("Book", "book.epub", {"chapter": 0, "scroll": 0.4, "zoom": 1.25})
(root / "workspace.json").write_text(json.dumps({
    "schema": 1, "module": "Book", "sidebar": True,
    "workspace": {"focused": 1, "zoomed": True, "tree": {
        "direction": "h", "sizes": [500, 500], "children": [
            {"order": 0, "pane": {"component": "viewer", "module": "Book", "filename": "book.epub"}},
            {"order": 1, "pane": {"component": "vocab"}}
        ]}}
}), encoding="utf-8")

window = MainWindow(root)
window.show()
assert window.workspace.is_zoomed
viewer = next(pane.component for pane in window.workspace._panes if isinstance(pane.component, EpubViewer))
assert not viewer.isVisible()

def wait_for(check):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Timed out: action={action}, visible={viewer.isVisible()}, title={viewer._web.title()}, cached_scroll={viewer._web.page().scrollPosition().y()}, cached_height={viewer._web.page().contentsSize().height()}, location={viewer.source_location()}")

def js(code):
    result = []
    viewer._run_js(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

def cached_fraction():
    page = viewer._web.page()
    return page.scrollPosition().y() / max(1, page.contentsSize().height())

wait_for(lambda: viewer._web.title() == "Book" and not viewer._web.page().isLoading())
assert js('document.readyState') == "complete"
# Let successful-load callbacks finish while this pane remains hidden.
QTest.qWait(100)
assert not viewer.isVisible()

expected = 0.4
if action != "hidden":
    window.workspace.toggle_zoom()
    wait_for(lambda: viewer.isVisible() and viewer._web.page().contentsSize().height() > 0)
    wait_for(lambda: abs(cached_fraction() - 0.4) < 0.02)
    expected = 0.7 if action == "scroll" else 0.0
    js(f'window.scrollTo(0, document.scrollingElement.scrollHeight * {expected}); true;')
    wait_for(lambda: abs(cached_fraction() - expected) < 0.02)
    wait_for(lambda: abs(viewer.source_location()["scroll"] - expected) < 0.02)

assert window.close()
saved = data.storage.load_reading_pos("Book", "book.epub")
window.deleteLater()
app.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
assert saved["chapter"] == 0
assert abs(saved["scroll"] - expected) < 0.02, (action, expected, saved)
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), action],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
