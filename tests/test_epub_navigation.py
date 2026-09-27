"""Keep real EPUB navigation and large chapters working in Chromium."""
import os
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")


def test_epub_uses_canonical_temp_directory(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    import zipfile
    from vocab.gui.viewers import EpubBook

    (tmp_path / "alias_parent").mkdir()
    root = tmp_path / "extracted"
    root.mkdir()
    alias = str(tmp_path / "alias_parent" / ".." / "extracted")
    monkeypatch.setattr("vocab.gui.viewers.tempfile.TemporaryDirectory",
                        lambda **kwargs: SimpleNamespace(name=alias, cleanup=lambda: None))
    path = tmp_path / "book.epub"
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="one.html"/></manifest><spine><itemref idref="one"/></spine></package>')
        book.writestr("one.html", "<html><body>Text</body></html>")
    book = EpubBook(str(path))
    assert book.tmpdir == str(root.resolve())
    assert Path(book.chapters[0][1]).relative_to(book.tmpdir).as_posix() == "one.html"


def test_epub_links_large_xhtml_assets_and_load_errors(tmp_path):
    script = r'''
import sys
import zipfile
from pathlib import Path
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
path = data.paths.documents_dir("Book") / "linked.epub"
head = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{}</title><link rel="stylesheet" href="../style.css" /></head><body>'
with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="Text/one.xhtml"/><item id="two" href="Text/two%20chapter.xhtml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
    book.writestr("style.css", '#target { color: rgb(12, 34, 56); }')
    book.writestr("image.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="13"><rect width="12" height="13" fill="red"/></svg>')
    book.writestr("Text/one.xhtml", head.format("one") + '<a id="next" href="two%20chapter.xhtml#target">Next chapter</a><a id="missing" href="missing.xhtml">Missing chapter</a></body></html>')
    # This valid chapter exceeds setContent's limit even before URL encoding.
    book.writestr("Text/two chapter.xhtml", head.format("two") + '<!--' + 'x' * 3000000 + '--><p id="top">Top</p>' + '<p>Text for scrolling</p>' * 200 + '<p id="target">Café passage</p><img id="image" src="../image.svg"/><a id="up" href="#top">Top</a><a id="back" href="one.xhtml">Previous chapter</a><a id="again" href="two%20chapter.xhtml#target">Same chapter</a><a id="footnote" href="notes%2520%23part.xhtml#note">Footnote</a><a id="outside" href="../../outside.html">Outside book</a></body></html>')
    book.writestr("Text/notes%20#part.xhtml", head.format("notes") + '<p id="note">Footnote text</p><a id="return" href="two%20chapter.xhtml#target">Return</a></body></html>')

ctx = WorkspaceContext(data)
viewer = EpubViewer(str(path), ctx)
viewer.resize(900, 600)
viewer.show()

def wait_for(check):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Timed out: title={viewer._web.title()}, chapter={viewer._idx}, error={viewer._error.text()}")

def loaded(title):
    return viewer._web.title() == title and not viewer._web.page().isLoading()

def js(code):
    result = []
    viewer._web.page().runJavaScript(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

wait_for(lambda: loaded("one"))
js('document.getElementById("next").click(); true;')
wait_for(lambda: loaded("two") and viewer._web.url().fragment() == "target")
wait_for(lambda: viewer._web.page().scrollPosition().y() > 0)
assert viewer._idx == viewer._picker.currentIndex() == 1
assert viewer._prev.isEnabled() and not viewer._next.isEnabled()
assert viewer._error.isHidden(), viewer._error.text()
text = js('document.getElementById("target").textContent')
assert text == "Café passage", (text, js('document.characterSet'))
assert js('getComputedStyle(document.getElementById("target")).color') == "rgb(12, 34, 56)"
assert js('document.getElementById("image").naturalWidth') == 12
assert js('document.getElementById("target").getBoundingClientRect().top < innerHeight')

captures = []
ctx.capture_requested.connect(lambda *args: captures.append(args))
js('var range=document.createRange(); range.selectNodeContents(document.getElementById("target")); var selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range); true;')
wait_for(lambda: viewer._web.selectedText() == "Café passage")
viewer._capture()
wait_for(lambda: bool(captures))
assert captures[-1][0:2] == ("Book", "Café passage")
assert captures[-1][2]["chapter"] == 1
assert captures[-1][2]["scroll"] > 0
viewer.save_position()
saved = data.storage.load_reading_pos("Book", "linked.epub")
assert saved["chapter"] == 1 and saved["scroll"] > 0

# Both short and explicit same-chapter fragment links must scroll correctly.
js('document.getElementById("up").click(); true;')
wait_for(lambda: viewer._web.url().fragment() == "top")
assert js('document.getElementById("top").getBoundingClientRect().top >= 0')
js('document.getElementById("again").click(); true;')
wait_for(lambda: viewer._web.url().fragment() == "target")
wait_for(lambda: viewer._web.page().scrollPosition().y() > 0)
assert viewer.source_location()["chapter"] == 1
js('document.getElementById("back").click(); true;')
wait_for(lambda: loaded("one"))
assert viewer._idx == viewer._picker.currentIndex() == 0
assert not viewer._prev.isEnabled() and viewer._next.isEnabled()

# The saved capture still navigates to its chapter and scroll position.
viewer.go_to(captures[-1][2])
wait_for(lambda: loaded("two"))
wait_for(lambda: viewer._web.page().scrollPosition().y() > 0)
assert viewer.source_location()["chapter"] == 1
assert viewer._error.isHidden(), viewer._error.text()

# Nonspine footnotes retain their file when capturing, reopening or returning.
js('document.getElementById("footnote").click(); true;')
wait_for(lambda: loaded("notes"))
footnote = viewer.source_location()
assert footnote["href"] == "Text/notes%2520%23part.xhtml"
viewer.save_position()
reopened = EpubViewer(str(path), ctx)
reopened.resize(900, 600)
reopened.show()
wait_for(lambda: reopened._web.title() == "notes" and not reopened._web.page().isLoading())
assert reopened.source_location()["href"] == "Text/notes%2520%23part.xhtml"
reopened.close()
reopened.deleteLater()
app.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
js('document.getElementById("return").click(); true;')
wait_for(lambda: loaded("two"))
assert "href" not in viewer.source_location()
viewer.go_to(footnote)
wait_for(lambda: loaded("notes"))
viewer.go_to(captures[-1][2])
wait_for(lambda: loaded("two"))

js('document.getElementById("outside").click(); true;')
wait_for(lambda: not viewer._error.isHidden())
assert "outside this EPUB" in viewer._error.text()
assert viewer._web.title() == "two"
assert viewer.source_location()["chapter"] == 1

# Exercise a real browser load failure, not only an exception opening a file.
Path(viewer._web.url().toLocalFile()).unlink()
viewer._web.reload()
wait_for(lambda: "Could not read chapter" in viewer._error.text())
assert not viewer._error.isHidden()
assert viewer._web.isHidden()
viewer._go(-1)
wait_for(lambda: loaded("one"))
wait_for(lambda: not viewer._web.isHidden())
assert viewer._error.isHidden()

js('document.getElementById("missing").click(); true;')
wait_for(lambda: not viewer._error.isHidden())
assert "Could not read chapter" in viewer._error.text()
assert viewer._web.title() == "one"
assert viewer._idx == viewer._picker.currentIndex() == 0
assert viewer.source_location()["chapter"] == 0

viewer.close()
viewer.deleteLater()
app.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_scroll_restore_exhaustion_uses_live_position_for_capture_and_save(tmp_path):
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
path = data.paths.documents_dir("Book") / "snap.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="start" href="start.html"/><item id="snap" href="snap.html"/></manifest><spine><itemref idref="start"/><itemref idref="snap"/></spine></package>')
    book.writestr("start.html", '<html><head><title>start</title></head><body>Start</body></html>')
    book.writestr("snap.html", '<html><head><title>snap</title><style>html {scroll-snap-type:y mandatory} body {margin:0} section {height:100px;scroll-snap-align:start}</style></head><body>' + ''.join(f'<section id="s{i}">Passage {i}</section>' for i in range(90)) + '</body></html>')

ctx = WorkspaceContext(data)
viewer = EpubViewer(str(path), ctx)
viewer.resize(900, 600)
viewer.show()

attempts = []

def wait_for(check):
    for _ in range(400):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Timed out: pending={viewer._pending_scroll}, title={viewer._web.title()}, attempts={attempts}")

def js(code):
    result = []
    viewer._web.page().runJavaScript(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

try:
    wait_for(lambda: viewer._web.title() == "start" and not viewer._web.page().isLoading())
    restore = viewer._restore_scroll
    def counted_restore(generation, attempt=0):
        attempts.append(attempt)
        restore(generation, attempt)
    viewer._restore_scroll = counted_restore

    # Mandatory scroll snapping prevents the requested 37% position from being
    # reached exactly. Exercise the real renderer and the entire retry budget.
    viewer.go_to({"chapter": 1, "scroll": 0.37})
    wait_for(lambda: attempts and attempts[-1] == viewer.MAX_RESTORE_RETRIES)
    wait_for(lambda: viewer._pending_scroll == 0)

    # Reading on after an unsuccessful restoration must supersede the old 37%.
    js('window.scrollTo(0,7200); true;')
    wait_for(lambda: viewer._web.page().scrollPosition().y() / viewer._web.page().contentsSize().height() > 0.7)
    actual = viewer._web.page().scrollPosition().y() / viewer._web.page().contentsSize().height()
    assert abs(viewer.source_location()["scroll"] - actual) < 0.000001

    captures = []
    ctx.capture_requested.connect(lambda *args: captures.append(args))
    js('var range=document.createRange(); range.selectNodeContents(document.getElementById("s72")); var selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range); true;')
    wait_for(lambda: viewer._web.selectedText() == "Passage 72")
    viewer._capture()
    wait_for(lambda: bool(captures))
    assert captures[-1][0:2] == ("Book", "Passage 72")
    assert captures[-1][2]["chapter"] == 1
    assert abs(captures[-1][2]["scroll"] - actual) < 0.000001
    viewer.save_position()
    saved = data.storage.load_reading_pos("Book", "snap.epub")
    assert saved["chapter"] == 1
    assert abs(saved["scroll"] - actual) < 0.000001
finally:
    viewer.close()
    viewer.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
