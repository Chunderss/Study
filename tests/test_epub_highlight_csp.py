"""EPUB highlights stay visible in chapters whose Content-Security-Policy
forbids inline styles, while the book's policy stays in force."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

SCRIPT = r'''
import json
import sys
import zipfile
from PySide6.QtCore import QEvent, QRect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.core.learning import LearningStore
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer

app = QApplication([])
data = App(sys.argv[1])
data.cmd_create("Book")
data.cmd_use("Book")
ctx = WorkspaceContext(data)
store = LearningStore(data.storage)
xhtml = sys.argv[2] == "xhtml"
path = data.paths.documents_dir("Book") / "book.epub"
head = ('<meta http-equiv="Content-Security-Policy" content="default-src \'self\'"/>'
        '<link rel="stylesheet" type="text/css" href="style.css"/><title>Policy</title>')
body = ('<p id="styled" style="color: rgb(1, 2, 3)">Styled by an inline attribute.</p>'
        '<p id="passage">Highlighted passage text here.</p>'
        + '<p>Other text in the chapter.</p>' * 40)
if xhtml:
    chapter = ('<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml">'
               f'<head>{head}</head><body>{body}</body></html>')
else:
    chapter = f'<!DOCTYPE html><html><head>{head}</head><body>{body}</body></html>'
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="c" href="chapter.xhtml"/></manifest><spine><itemref idref="c"/></spine></package>')
    book.writestr("chapter.xhtml", chapter)
    book.writestr("style.css", "#passage { font-size: 40px; }")  # the book's own sheet may style
views = []

def wait_for(check, failure=lambda: "Timed out"):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(failure())

def js(viewer, code):
    result = []
    viewer._run_js(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

def open_viewer(source=None):
    viewer = EpubViewer(str(path), ctx, module="Book", source=source)
    views.append(viewer)
    viewer.resize(900, 700)
    viewer.show()
    wait_for(lambda: viewer._document_loaded and viewer._web.title() == "Policy"
             and not viewer._web.page().isLoading())
    return viewer

def highlight(viewer, name):
    return json.loads(js(viewer, """JSON.stringify((() => {
        const style = getComputedStyle(document.getElementById('passage'), '::highlight(%s)');
        return {background: style.backgroundColor, decoration: style.textDecorationLine,
                size: (CSS.highlights.get('%s') || new Set()).size};
    })())""" % (name, name)))

def passage_rect(viewer):
    rect = json.loads(js(viewer, """JSON.stringify((() => {
        const range = document.createRange();
        range.selectNodeContents(document.getElementById('passage').firstChild);
        return range.getBoundingClientRect().toJSON();
    })())"""))
    zoom = viewer._web.zoomFactor()
    # The middle of the passage's line box, clear of its edges.
    return QRect(int(rect["left"] * zoom) + 4, int(rect["top"] * zoom) + 4,
                 int(rect["width"] * zoom) - 8, int(rect["height"] * zoom) - 8)

def mean_color(viewer, rect):
    image = viewer._web.grab(rect).toImage()
    totals = [0, 0, 0]
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            color = image.pixelColor(x, y)
            for i, value in enumerate((color.red(), color.green(), color.blue())):
                totals[i] += value
    count = len(range(0, image.height(), 2)) * len(range(0, image.width(), 2))
    return [total / count for total in totals]

try:
    viewer = open_viewer()
    rect = passage_rect(viewer)
    assert rect.width() > 100 and rect.height() > 20, rect
    QTest.qWait(200)
    plain = mean_color(viewer, rect)
    # Capture the passage through the reader's own Capture button path.
    js(viewer, """(() => { const range = document.createRange();
        range.selectNodeContents(document.getElementById('passage').firstChild);
        getSelection().removeAllRanges(); getSelection().addRange(range); return true; })()""")
    captures = []
    ctx.capture_requested.connect(lambda *args: captures.append(args))
    viewer._capture()
    wait_for(lambda: bool(captures))
    js(viewer, "getSelection().removeAllRanges(); true;")
    _module, quote, source = captures[0]
    item = store.save_capture("Book", kind="concept", prompt="Why?", quote=quote, source=source)
    ctx.learning_changed.emit()
    wait_for(lambda: highlight(viewer, "vocab-study-saved")["size"] == 1)
    assert highlight(viewer, "vocab-study-saved")["background"] == "rgba(244, 204, 69, 0.45)", \
        highlight(viewer, "vocab-study-saved")
    # The yellow is painted: blue drops behind the passage.
    wait_for(lambda: mean_color(viewer, rect)[2] < plain[2] - 20,
             lambda: f"plain={plain}, highlighted={mean_color(viewer, rect)}")
    for _ in range(3):
        ctx.learning_changed.emit()
    QTest.qWait(200)
    assert js(viewer, "document.adoptedStyleSheets.length") == 1
    assert js(viewer, "document.querySelectorAll('style').length") == 0
    # The book's policy still applies to the book.
    assert js(viewer, "getComputedStyle(document.getElementById('styled')).color") != "rgb(1, 2, 3)"

    # The passage Source opens shows the active highlight.
    active = open_viewer(source=dict(source, capture_id=item["id"]))
    wait_for(lambda: highlight(active, "vocab-study-active")["size"] == 1)
    style = highlight(active, "vocab-study-active")
    assert style["background"] == "rgba(242, 157, 54, 0.72)" and "underline" in style["decoration"], style

    # Deleting the capture removes the paint again.
    store.delete("Book", item["id"], item["revision"])
    ctx.learning_changed.emit()
    wait_for(lambda: highlight(viewer, "vocab-study-saved")["size"] == 0)
    wait_for(lambda: abs(mean_color(viewer, rect)[2] - plain[2]) < 5,
             lambda: f"plain={plain}, after delete={mean_color(viewer, rect)}")
finally:
    for view in views:
        view.close()
        view.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''


@pytest.mark.parametrize("markup", ["html", "xhtml"])
def test_highlights_paint_in_chapters_whose_csp_forbids_inline_styles(tmp_path, markup):
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags + " --disable-gpu --disable-smooth-scrolling"
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", SCRIPT, str(tmp_path), markup],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
