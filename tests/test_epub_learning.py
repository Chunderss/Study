"""Exercise the real Chromium selection callback in its own Qt process."""
import os
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")


def test_epub_capture_and_source_navigation(tmp_path):
    script = r'''
import sys
import zipfile
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer
app = QApplication([])
data = App(sys.argv[1])
data.cmd_create("Book")
data.cmd_use("Book")
path = data.paths.documents_dir("Book") / "book.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="one.html"/><item id="two" href="two.html"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
    for name in ("one", "two"):
        book.writestr(name + ".html", f'<html><head><title>{name}</title></head><body><p id="passage">Passage {name}</p>' + '<p>More text for scrolling</p>' * 200 + '</body></html>')
ctx = WorkspaceContext(data)
viewer = EpubViewer(str(path), ctx)
viewer.resize(900, 600)
viewer.show()

def wait_for(check):
    for _ in range(500):
        if check(): return
        QTest.qWait(20)
    raise AssertionError("Browser callback did not finish")

wait_for(lambda: viewer._web.title() == "one")
loaded = []
viewer._web.loadFinished.connect(lambda ok: loaded.append(ok))
viewer.go_to({"chapter": 1, "scroll": 0.25})
wait_for(lambda: True in loaded and viewer._web.title() == "two")
wait_for(lambda: viewer._web.page().scrollPosition().y() > 0)
selected = []
viewer._web.page().runJavaScript('var r=document.createRange(); r.selectNodeContents(document.getElementById("passage")); var s=window.getSelection(); s.removeAllRanges(); s.addRange(r); true;', lambda value: selected.append(value))
wait_for(lambda: selected and viewer._web.selectedText() == "Passage two")
data.cmd_create("Other")
data.cmd_use("Other")
captures = []
ctx.capture_requested.connect(lambda *args: captures.append(args))
viewer._capture()
assert captures[0][0] == "Book"
assert captures[0][1] == "Passage two"
assert captures[0][2]["chapter"] == 1
assert captures[0][2]["filename"] == "book.epub"
assert captures[0][2]["scroll"] > 0
viewer.save_position()
assert data.storage.load_reading_pos("Book", "book.epub")["chapter"] == 1
assert not data.storage.load_reading_pos("Other", "book.epub")
viewer.close()
viewer.deleteLater()
app.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
