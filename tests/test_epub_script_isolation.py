"""EPUB content must not run its own scripts, while the reader keeps working.

A book is untrusted input loaded from file:// pages; its scripts could read
local files and send them elsewhere. The reader's own scripts run in Qt's
ApplicationWorld, which keeps working with the book's JavaScript disabled.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = r'''
import json
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer

import http.server
import threading

requests = []
class Recorder(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        requests.append(self.path)
        self.send_response(404)
        self.end_headers()
    def log_message(self, *args):
        pass
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
threading.Thread(target=server.serve_forever, daemon=True).start()
remote = f"http://127.0.0.1:{server.server_address[1]}"

app = QApplication([])
root = Path(sys.argv[1])
enable_book_scripts = sys.argv[2] == "enabled"
sentinel = root / "outside-the-book.txt"
sentinel.write_text("CANARY-SECRET", encoding="utf-8")
data = App(root / "home")
data.cmd_create("Book")
data.cmd_use("Book")

image = QImage(300, 200, QImage.Format_RGB32)
image.fill(QColor("steelblue"))
png = QByteArray()
buffer = QBuffer(png)
buffer.open(QIODevice.WriteOnly)
image.save(buffer, "PNG")

chapter = f"""<html><head><title>Isolated</title>
<link rel="stylesheet" href="style.css"/>
<link rel="stylesheet" href="{remote}/remote.css"/>
<script>document.title = "HACKED-INLINE";</script>
<script src="evil.js"></script>
<script>fetch({json.dumps(sentinel.as_uri())}).then(r => r.text())
    .then(t => {{ document.body.dataset.leak = t; }});</script>
</head><body onload="document.title = 'HACKED-ONLOAD'">
<img src="missing.png" onerror="document.title = 'HACKED-HANDLER'"/>
<iframe srcdoc="&lt;script&gt;parent.document.title = 'HACKED-IFRAME'&lt;/script&gt;"></iframe>
<p id="styled" class="styled">Styled ünïcödé text ✓</p>
<img id="pic" src="pic.png"/>
<img src="{remote}/tracker.png"/>
<noscript><p id="fallback">Fallback words</p></noscript>
<p id="sentence">The quick brown fox jumps. Another sentence follows here.</p>
</body></html>"""
path = data.paths.documents_dir("Book") / "isolated.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="one" href="one.html"/></manifest><spine><itemref idref="one"/></spine></package>')
    book.writestr("one.html", chapter)
    book.writestr("style.css", "p.styled { color: rgb(12, 34, 56); }")
    book.writestr("evil.js", "document.title = 'HACKED-FILE';")
    book.writestr("pic.png", bytes(png))

ctx = WorkspaceContext(data)
captures, words = [], []
ctx.capture_requested.connect(lambda *args: captures.append(args))
ctx.add_word_requested.connect(lambda *args: words.append(args))
viewer = EpubViewer(str(path), ctx)
if enable_book_scripts:  # regression-check mode: the checks below must fail
    from PySide6.QtWebEngineCore import QWebEngineSettings
    viewer._web.settings().setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
    viewer._load(0)
viewer.resize(900, 700)
viewer.show()

def wait_for(check, what):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Timed out waiting for {what}")

def app_js(code):
    result = []
    viewer._run_js(code, lambda value: result.append(value))
    wait_for(lambda: bool(result), "reader script")
    return result[0]

def page_js(code):
    result = []
    viewer._web.page().runJavaScript(code, lambda value: result.append(value))
    wait_for(lambda: bool(result), "page script")
    return result[0]

def select(element_id, start=None, end=None):
    app_js(f"""(() => {{
        const node = document.getElementById({json.dumps(element_id)}).firstChild;
        const range = document.createRange();
        if ({json.dumps(start)} === null) range.selectNodeContents(node);
        else {{ range.setStart(node, {json.dumps(start)}); range.setEnd(node, {json.dumps(end)}); }}
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(range);
        return true;
    }})()""")

try:
    wait_for(lambda: viewer._document_loaded and not viewer._web.page().isLoading(), "chapter load")
    QTest.qWait(400)  # give any book script, handler or frame time to act
    state = json.loads(app_js("""JSON.stringify({
        title: document.title,
        leak: document.body.dataset.leak || null,
        color: getComputedStyle(document.getElementById('styled')).color,
        text: document.getElementById('styled').textContent,
        image: document.getElementById('pic').naturalWidth,
        noscript: !!document.getElementById('fallback'),
    })"""))
    print("STATE", json.dumps(state), flush=True)
    assert state["title"] == "Isolated", state
    assert state["leak"] is None, state
    assert viewer._web.title() == "Isolated"
    # The book's own world does not run code at all.
    assert page_js("1 + 1") != 2
    # Book resources still load and render.
    assert state["color"] == "rgb(12, 34, 56)", state
    assert state["image"] == 300, state
    assert state["text"] == "Styled ünïcödé text ✓", state
    # Nothing is fetched from outside the book (tracking pixels, remote CSS).
    assert requests == [], requests
    # <noscript> content is shown now, and it can be captured.
    assert state["noscript"], state
    select("fallback")
    viewer._capture()
    wait_for(lambda: captures, "capture")
    assert captures[-1][1] == "Fallback words", captures
    assert captures[-1][2]["anchor"]["exact"] == "Fallback words", captures
    # Add word still reads the selection and its surrounding text. (Which
    # sentence it picks on very short pages is audit item L12, not isolation.)
    select("sentence", 16, 19)
    viewer._add_selection()
    wait_for(lambda: words, "add word")
    word, sentence, module = words[-1]
    assert (word, module) == ("fox", "Book") and "The quick brown fox jumps." in sentence, words
    print("ISOLATION-OK", flush=True)
finally:
    viewer.close()
    viewer.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''


def run(tmp_path, mode):
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    return subprocess.run([sys.executable, "-c", SCRIPT, str(tmp_path), mode], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=60)


def test_book_scripts_do_not_run_and_reader_features_still_work(tmp_path):
    result = run(tmp_path, "disabled")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ISOLATION-OK" in result.stdout


def test_the_same_book_does_run_scripts_when_javascript_is_enabled(tmp_path):
    # Keeps the test above meaningful: with book JavaScript on, this book does
    # change the title and read the file outside the book.
    result = run(tmp_path, "enabled")
    assert result.returncode != 0
    assert '"leak": "CANARY-SECRET"' in result.stdout, result.stdout + result.stderr
