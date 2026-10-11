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


def test_legacy_quotes_match_rendered_text_without_conflating_words():
    script = r'''
import json
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.gui.epub_highlights import render_highlights_js

app = QApplication([])
web = QWebEngineView()
web.resize(850, 900)
web.show()
loaded = []
web.loadFinished.connect(loaded.append)
image = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10'/%3E"
web.setHtml("""<html><head><title>Legacy</title></head><body>
<p id="wrapped">The quick brown
   fox jumps over</p>
<p id="nbsp">Ten&nbsp;kilometres and two&nbsp;&nbsp;spaces</p>
<div id="paragraphs"><p>End of one paragraph.</p>
<p>Start of the next.</p></div>
<p id="br">First line<br/>second line</p>
<table id="cells"><tr><td>Cell one</td><td>cell two</td></tr></table>
<ul id="list"><li>Item one</li><li>item two</li></ul>
<p id="ruby"><ruby>漢<rt>kan</rt></ruby>字 reading</p>
<p id="emoji">Smile 😀&nbsp;now</p>
<p id="drop"><span style="float:left;font-size:3em">T</span>he drop cap opening line.</p>
<p id="image">picture<img src="IMAGE" style="display:block;width:10px;height:10px" alt="">caption</p>
<div id="rule">Above the rule<hr/>below the rule</div>
<p id="nowhere">It was nowhere to be found.</p>
<div id="split"><p>some</p><p>where</p></div>
<p id="once">Repeated twice here.</p>
<p id="twice">Repeated
   twice here.</p>
<p hidden>Only hidden text here.</p>
</body></html>""".replace("IMAGE", image))

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

def legacy_quote(element_id):
    """What older versions saved: the reader's selected text, trimmed."""
    js("""(() => { const range = document.createRange();
        range.selectNodeContents(document.getElementById(%s));
        getSelection().removeAllRanges(); getSelection().addRange(range); return true; })()"""
       % json.dumps(element_id))
    wait_for(lambda: web.selectedText().strip() != "")
    quote = web.selectedText().strip()
    js("getSelection().removeAllRanges(); true;")
    wait_for(lambda: web.selectedText() == "")
    return quote

wait_for(lambda: True in loaded)
QTest.qWait(100)
resolvable = ["wrapped", "nbsp", "paragraphs", "br", "cells", "list", "ruby", "emoji", "drop",
              "image", "rule"]
quotes = {element_id: legacy_quote(element_id) for element_id in resolvable}
# The rendered text differs from the raw text nodes the anchors index.
assert quotes["wrapped"] == "The quick brown fox jumps over"
assert quotes["nbsp"] == "Ten kilometres and two  spaces"
assert "\n" in quotes["paragraphs"] and "\n" in quotes["br"] and "\t" in quotes["cells"]
assert "￼" in quotes["image"]
records = [{"id": element_id, "anchor": {"exact": quote}} for element_id, quote in quotes.items()]
records += [
    {"id": "joined words", "anchor": {"exact": "was now here to"}},
    {"id": "split word", "anchor": {"exact": "somewhere"}},
    {"id": "repeated", "anchor": {"exact": legacy_quote("twice")}},
    {"id": "hidden", "anchor": {"exact": "Only hidden text here."}},
    # Anchored captures keep exact raw matching.
    {"id": "anchored", "anchor": {"version": 1, "exact": "The quick brown fox jumps over",
                                  "prefix": "\n", "suffix": "\n", "start": 1, "end": 31}},
]
js("""window.textScans = 0; const createWalker = document.createTreeWalker.bind(document);
document.createTreeWalker = (...args) => { ++window.textScans; return createWalker(...args); }; true;""")
report = json.loads(js(render_highlights_js(records, focus_id="paragraphs")))
assert report["rendered"] == resolvable, report
assert report["unresolved"] == ["joined words", "split word", "repeated", "hidden", "anchored"], report
assert report["focused"]
assert js("window.textScans") == 1
highlights = json.loads(js("""JSON.stringify([...CSS.highlights.get('vocab-study-saved')].map(range => ({
    text: range.toString(),
    start: range.startContainer.parentElement.closest('[id]').id,
    end: range.endContainer.parentElement.closest('[id]').id})))"""))
assert len(highlights) == len(resolvable)
for element_id, highlight in zip(resolvable, highlights):
    # Each covers the whole passage in the original DOM text, line breaks included.
    assert highlight["start"] == highlight["end"] == element_id, (element_id, highlight)
    whole = js("document.getElementById(%s).textContent" % json.dumps(element_id))
    assert highlight["text"].split() == whole.split(), (element_id, highlight, whole)
assert highlights[0]["text"] == "The quick brown\n   fox jumps over"
assert highlights[1]["text"] == "Ten kilometres and two  spaces"
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


def test_highlight_sheet_keeps_the_books_adopted_sheets_and_falls_back_to_a_style_element():
    script = r'''
import json
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.gui.epub_highlights import render_highlights_js

app = QApplication([])
web = QWebEngineView()
web.resize(850, 600)
web.show()
READER = QWebEngineScript.ScriptWorldId.ApplicationWorld.value
PAGE = """<html><head><title>Sheets</title><script>
const sheet = new CSSStyleSheet();
sheet.replaceSync('#book { color: rgb(10, 120, 30); }');
document.adoptedStyleSheets = [sheet];
</script></head><body><p id="book">A passage the book styles itself.</p></body></html>"""

def wait_for(check):
    for _ in range(500):
        if check(): return
        QTest.qWait(20)
    raise AssertionError("Chromium callback did not finish")

def js(code, world=READER):
    results = []
    web.page().runJavaScript(code, world, results.append)
    wait_for(lambda: bool(results))
    return results[0]

def load():
    loaded = []
    web.loadFinished.connect(loaded.append)
    web.setHtml(PAGE)
    wait_for(lambda: True in loaded)
    web.loadFinished.disconnect()

def highlight_style():
    return js("""(() => {
        const style = getComputedStyle(document.getElementById('book'), '::highlight(vocab-study-saved)');
        return style.backgroundColor;
    })()""")

records = [{"id": "passage", "anchor": {"exact": "passage the book"}}]
load()
for _ in range(2):  # refreshing again adds nothing
    assert json.loads(js(render_highlights_js(records)))["rendered"] == ["passage"]
assert js("document.adoptedStyleSheets.length") == 2
assert js("document.adoptedStyleSheets[0].cssRules[0].selectorText") == "#book"
assert js("getComputedStyle(document.getElementById('book')).color") == "rgb(10, 120, 30)"
assert js("document.querySelectorAll('style').length") == 0
assert highlight_style() == "rgba(244, 204, 69, 0.45)"

# Without constructed sheets, a style element carries the rules, once.
load()
js("Object.defineProperty(Document.prototype, 'adoptedStyleSheets', {value: undefined}); true;")
for _ in range(2):
    assert json.loads(js(render_highlights_js(records)))["rendered"] == ["passage"]
assert js("document.querySelectorAll('style').length") == 1
assert js("document.adoptedStyleSheets.length", world=0) == 1  # the book's only
assert highlight_style() == "rgba(244, 204, 69, 0.45)"
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
