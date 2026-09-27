"""Open captured passages through the real workspace source-link handler."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")


@pytest.mark.parametrize("source_kind", ["chapter", "footnote"])
def test_epub_source_location_applies_on_first_load_and_reader_replacement(tmp_path, source_kind):
    script = r'''
import faulthandler
faulthandler.enable()
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import EpubViewer

app = QApplication([])
window = MainWindow(Path(sys.argv[1]))
window.app.cmd_create("Book")
window.app.cmd_use("Book")
window.ctx.module_changed.emit("Book")
path = window.app.paths.documents_dir("Book") / "book.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="one.html"/><item id="two" href="two.html"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
    for name, title in (("one.html", "One"), ("two.html", "Two"),
                        ("old-notes.xhtml", "Old notes"), ("notes%20#part.xhtml", "Notes")):
        book.writestr(name, f'<html><head><title>{title}</title></head><body>' + '<p>Passage for reading.</p>' * 400 + '</body></html>')

# The captured source replaces the saved location, including a stale footnote,
# while preserving the reader's chosen zoom level.
window.app.storage.save_reading_pos("Book", "book.epub", {
    "chapter": 0, "scroll": 0.1, "zoom": 1.5, "href": "old-notes.xhtml"})
source = {"kind": "epub", "filename": "book.epub", "chapter": 1, "scroll": 0.65}
expected_title = "Two"
if sys.argv[2] == "footnote":
    source["href"] = "notes%2520%23part.xhtml"
    expected_title = "Notes"
window.show()
app.processEvents()
window._open_source("Book", source)
viewer = window.workspace.focused.component
assert isinstance(viewer, EpubViewer)

def wait_for(check):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Source jump did not finish: title={viewer._web.title()}, cached_scroll={viewer._web.page().scrollPosition().y()}, height={viewer._web.page().contentsSize().height()}, pending={viewer._pending_scroll}, source={viewer.source_location()}")

def fraction():
    page = viewer._web.page()
    return page.scrollPosition().y() / max(1, page.contentsSize().height())

wait_for(lambda: viewer._web.title() == expected_title and not viewer._web.page().isLoading())
wait_for(lambda: abs(fraction() - 0.65) < 0.02)
wait_for(lambda: viewer._pending_scroll == 0)
assert viewer._zoom == 1.5
assert viewer.source_location()["chapter"] == 1
assert viewer.source_location().get("href") == source.get("href")
assert abs(viewer.source_location()["scroll"] - 0.65) < 0.02

# Follow another capture while the pane already contains an EPUB reader.
window._open_source("Book", {"kind": "epub", "filename": "book.epub", "chapter": 0, "scroll": 0.25})
viewer = window.workspace.focused.component
assert isinstance(viewer, EpubViewer)
wait_for(lambda: viewer._web.title() == "One" and not viewer._web.page().isLoading())
wait_for(lambda: abs(fraction() - 0.25) < 0.02)
wait_for(lambda: viewer._pending_scroll == 0)
assert viewer._zoom == 1.5
assert viewer.source_location()["chapter"] == 0
assert "href" not in viewer.source_location()
assert window.close()
saved = window.app.storage.load_reading_pos("Book", "book.epub")
assert saved["chapter"] == 0
assert abs(saved["scroll"] - 0.25) < 0.02
assert "href" not in saved
window.deleteLater()
app.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), source_kind],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
