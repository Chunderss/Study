"""Validate passage anchors and both highlight renderers in real Chromium."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")


def test_epub_anchor_unicode_context_overlap_and_safe_fallback():
    script = r'''
import json
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.gui.epub_highlights import SELECTION_ANCHOR_JS, render_highlights_js

app = QApplication([])
web = QWebEngineView()
web.resize(850, 600)
web.show()
loaded = []
web.loadFinished.connect(loaded.append)
web.setHtml('<html><head><title>Anchors</title></head><body><p id="first">😀 First repeated <em>phrase</em> tail.</p><p hidden>Hidden repeated phrase</p><div style="height:1800px"></div><p id="second">😀 Second repeated <em>phrase</em> tail.</p><p id="literal"></p></body></html>')

def wait_for(check):
    for _ in range(500):
        if check(): return
        QTest.qWait(20)
    raise AssertionError("Chromium callback did not finish")

def js(code):
    results = []
    web.page().runJavaScript(code, results.append)
    wait_for(lambda: bool(results))
    return results[0]

wait_for(lambda: True in loaded)
assert js("!!(CSS.highlights && window.Highlight)"), "Bundled Chromium must support CSS Custom Highlights"
# Empty chapters do not need a potentially expensive rendered-text scan.
js("""window.textScans = 0; const createWalker = document.createTreeWalker.bind(document);
document.createTreeWalker = (...args) => { ++window.textScans; return createWalker(...args); }; true;""")
assert json.loads(js(render_highlights_js([])))["rendered"] == []
assert js("window.textScans") == 0
original = js('document.body.textContent')
js("""var p=document.getElementById("second"); var r=document.createRange();
r.setStart(p.firstChild, p.firstChild.data.indexOf("repeated"));
r.setEnd(p.querySelector("em").firstChild, 6);
var s=getSelection(); s.removeAllRanges(); s.addRange(r); true;""")
anchor = json.loads(js(SELECTION_ANCHOR_JS))
assert anchor["exact"] == "repeated phrase"
assert anchor["quote"] == "repeated phrase"
expected_prefix = "😀 First repeated phrase tail.😀 Second "
assert anchor["start"] == len(expected_prefix.encode("utf-16-le")) // 2
assert anchor["end"] - anchor["start"] == len("repeated phrase")
assert anchor["prefix"] == expected_prefix
assert "Hidden" not in anchor["prefix"]
assert anchor["scroll"] == 0

js("""var p=document.getElementById("second"); var r=document.createRange();
r.setStart(p.querySelector("em").firstChild, 0); r.setEnd(p.lastChild, 5);
var s=getSelection(); s.removeAllRanges(); s.addRange(r); true;""")
overlap = json.loads(js(SELECTION_ANCHOR_JS))
assert overlap["exact"] == "phrase tail"
records = [{"id": "passage", "anchor": anchor}, {"id": "overlap", "anchor": overlap},
           {"id": "ambiguous", "anchor": {"exact": "repeated phrase"}}]
js("window.textScans = 0; document.documentElement.style.scrollBehavior = 'smooth'; true;")
report = json.loads(js(render_highlights_js(records, active_id="overlap", focus_id="passage")))
assert report == {"supported": True, "renderer": "css", "rendered": ["passage", "overlap"],
                  "unresolved": ["ambiguous"], "focused": True}, report
assert js("window.textScans") == 1
assert json.loads(js("JSON.stringify([...CSS.highlights.get('vocab-study-saved')].map(r => r.toString()))")) == ["repeated phrase", "phrase tail"]
assert js("CSS.highlights.get('vocab-study-active').size") == 1
assert js("window.scrollY > 0")
assert js("getSelection().toString()") == "phrase tail"
assert js("document.body.textContent") == original

# Hidden Qt readers still have usable text ranges, but must defer source jumps.
web.hide()
wait_for(lambda: js("document.visibilityState") == "hidden")
report = json.loads(js(render_highlights_js(records, focus_id="passage")))
assert report["rendered"] == ["passage", "overlap"]
assert not report["focused"]
web.show()
wait_for(lambda: js("document.visibilityState") == "visible")

# The same context resolves after an earlier insertion invalidates the offsets.
js('document.body.prepend(document.createTextNode("New preface. ")); true;')
report = json.loads(js(render_highlights_js(records)))
assert report["rendered"] == ["passage", "overlap"]
assert js("[...CSS.highlights.get('vocab-study-saved')].every(r => r.startContainer.parentElement.closest('#second'))")

# The span fallback must segment overlaps, preserve selection, and stay idempotent.
js("Object.defineProperty(CSS, 'highlights', {value: undefined, configurable: true}); true;")
for _ in range(2):
    report = json.loads(js(render_highlights_js(records, active_id="overlap", focus_id="passage")))
    assert report["renderer"] == "spans" and report["focused"]
    assert report["rendered"] == ["passage", "overlap"]
    assert js("document.querySelectorAll('[data-vocab-study-highlight]').length") == 3
    assert js("document.querySelectorAll('[data-vocab-study-highlight=\"passage overlap\"]').length") == 1
    assert js("document.querySelectorAll('[data-vocab-study-active]').length") == 2
    assert js("getSelection().toString()") == "phrase tail"
    assert js("document.body.textContent") == "New preface. " + original

payload = '</script><img src=x onerror="window.highlightInjection=true">'
js('document.getElementById("literal").textContent = ' + json.dumps(payload) + '; true;')
report = json.loads(js(render_highlights_js([{"id": "literal", "anchor": {"exact": payload}}])))
assert report["rendered"] == ["literal"]
assert js("document.images.length") == 0
assert js("window.highlightInjection === undefined")
assert js("document.querySelector('[data-vocab-study-highlight]').textContent") == payload
report = json.loads(js(render_highlights_js([])))
assert report["rendered"] == []
assert js("document.querySelectorAll('[data-vocab-study-highlight]').length") == 0
assert js("document.body.textContent") == "New preface. " + original + payload
assert js("getSelection().toString()") == "phrase tail"

# Capture text keeps rendered paragraph breaks; anchor text keeps stable offsets.
js("""const block = document.createElement('div');
for (const text of ['First block', 'Second block']) {
    const paragraph = document.createElement('p'); paragraph.textContent = text;
    block.appendChild(paragraph);
}
document.body.appendChild(block);
const range = document.createRange(); range.selectNodeContents(block);
getSelection().removeAllRanges(); getSelection().addRange(range); true;""")
formatted = json.loads(js(SELECTION_ANCHOR_JS))
assert formatted["exact"] == "First blockSecond block"
assert formatted["quote"] == js("getSelection().toString().trim()")
assert "\n" in formatted["quote"]
web.close()
web.deleteLater()
app.sendPostedEvents(None, QEvent.DeferredDelete)
app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-c", script],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
