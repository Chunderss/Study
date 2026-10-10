"""Add word sends the sentence around the selected occurrence."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

from .test_epub_position_lifecycle import run_reader


def test_the_sentence_comes_from_the_selected_occurrence(tmp_path):
    run_reader(tmp_path, r'''
import json
body = ('<p id="banks">First bank deals money. The river bank supports trees.</p>'
        '<p id="art">Let us start. The art of reading.</p>'
        '<p id="em">A <em>quick</em> fox jumps. Another sentence.</p>'
        '<h2 id="heading">No full stop here</h2>'
        '<p id="tiny">Tiny bank.</p>'
        '<p id="ruby">Before it. The <ruby>bank<rt>gloss</rt></ruby> is near the river. Next one.</p>'
        '<p id="mono">\u6628\u65e5\u3001<ruby>\u9280<rt>\u304e\u3093</rt></ruby>'
        '<ruby>\u884c<rt>\u3053\u3046</rt></ruby>\u306b\u884c\u3063\u305f\u3002 Next.</p>')
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.xhtml", page.format("Main", body))

viewer = open_viewer()
wait_for(lambda: settled(viewer, "Main"), viewer)

def context(element, word, nth=0, backwards=False):
    js(viewer, """(() => {
        const words = [];
        const walker = document.createTreeWalker(document.getElementById(%s), NodeFilter.SHOW_TEXT);
        for (let node; (node = walker.nextNode());) {
            const pattern = new RegExp("\\\\b" + %s + "\\\\b", "g");
            for (let m; (m = pattern.exec(node.data));) words.push([node, m.index]);
        }
        const [node, offset] = words[%d], end = offset + %s.length;
        const selection = getSelection();
        if (%s) selection.setBaseAndExtent(node, end, node, offset);
        else selection.setBaseAndExtent(node, offset, node, end);
        return true;
    })()""" % (json.dumps(element), json.dumps(word), nth, json.dumps(word),
               "true" if backwards else "false"))
    return json.loads(js(viewer, viewer._SEL_JS))

assert context("banks", "bank", 1) == {"word": "bank", "sentence": "The river bank supports trees."}
assert context("banks", "bank", 1, backwards=True)["sentence"] == "The river bank supports trees."
assert context("banks", "bank", 0)["sentence"] == "First bank deals money."
assert context("art", "art")["sentence"] == "The art of reading."
assert context("em", "fox")["sentence"] == "A quick fox jumps."
assert context("em", "quick")["sentence"] == "A quick fox jumps."
assert context("tiny", "bank")["sentence"] == "Tiny bank."  # not the heading, not the title
assert context("ruby", "bank")["sentence"] == "The bank is near the river."
# A selection across mono ruby (two bases with their glosses) keeps neither gloss.
js(viewer, """(() => {
    const bases = document.querySelectorAll("#mono ruby");
    getSelection().setBaseAndExtent(bases[0].firstChild, 0, bases[1].firstChild, 1);
    return true;
})()""")
mono = json.loads(js(viewer, viewer._SEL_JS))
assert mono["word"] == "\u9280\u884c", mono
assert "\u304e" not in mono["sentence"] and mono["sentence"].startswith("\u6628\u65e5\u3001\u9280\u884c"), mono
''')


def test_add_word_keeps_the_sentence_the_reader_found(tmp_path):
    run_reader(tmp_path, r'''
import json
from vocab.core.models import Sense
body = '<p id="p">They raised the flag. His energy began to flag. It was late.</p>'
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.xhtml", page.format("Main", body))
data._lookup_senses = lambda word, sentence="": (
    [Sense("communicate or signal with a flag", "verb"), Sense("become less intense", "verb")],
    "wordnet", "")
ctx.add_word_requested.connect(lambda word, sentence, module: ctx.add_word(
    word, sentence=sentence, target=module))
logged = []
ctx.log.connect(logged.append)
viewer = open_viewer()
viewer._module = "Book"
wait_for(lambda: settled(viewer, "Main"), viewer)
js(viewer, """(() => {
    const node = document.getElementById("p").firstChild, at = node.data.lastIndexOf("flag");
    getSelection().setBaseAndExtent(node, at, node, at + 4);
    return true;
})()""")
viewer._add_selection()
wait_for(lambda: data.storage.load_words("Book").has("flag"))
entry = json.loads(data.paths.words_file("Book").read_text("utf-8"))["words"]["flag"]
assert entry["context"] == "His energy began to flag.", entry
assert any('Guessed from "His energy began to flag."' in line for line in logged), logged
''')


def test_add_word_in_a_reader_offers_its_senses_without_leaving_the_page(tmp_path):
    script = r'''
import json
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.core.models import Sense
from vocab.gui.main_window import MainWindow

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
root = Path(sys.argv[1])
data = App(root)
data.cmd_create("Book")
path = data.paths.documents_dir("Book") / "book.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="a" href="a.html"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.html", '<html><head><title>Main</title></head><body><p id="p">They raised the flag. His energy began to flag. It was late.</p></body></html>')

window = MainWindow(root)
window.app.cmd_use("Book")
window.ctx.module_changed.emit("Book")
window.app._lookup_senses = lambda word, sentence="": ([
    Sense("communicate or signal with a flag", "verb"), Sense("provide with a flag", "verb"),
    Sense("become less intense", "verb")], "wordnet", "")
window.show()
viewer = window._open_viewer(str(path))
native = window.windowHandle()
picker = window.sense_picker

def wait_for(check, what=""):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Timed out {what}: focus={app.focusWidget()}, message={window.message.text()}")

def js(code):
    result = []
    viewer._run_js(code, result.append)
    wait_for(lambda: bool(result), "for a script")
    return result[0]

def in_page():
    widget = app.focusWidget()
    return widget is not None and viewer.isAncestorOf(widget)

def raw():
    return json.loads(data.paths.words_file("Book").read_text("utf-8"))["words"].get("flag")

try:
    wait_for(lambda: viewer._web.title() == "Main" and not viewer._web.page().isLoading(), "for the page")
    window.activateWindow()
    viewer.focus_default()
    wait_for(in_page, "for the page to take focus")
    js("""(() => {
        const node = document.getElementById("p").firstChild, at = node.data.lastIndexOf("flag");
        getSelection().setBaseAndExtent(node, at, node, at + 4);
        return true;
    })()""")
    viewer._add_selection()
    wait_for(lambda: picker.isVisible(), "for the picker")
    assert raw()["context"] == "His energy began to flag.", raw()
    assert picker.context.text() == "“His energy began to flag.”"
    assert in_page(), app.focusWidget()  # reading goes on
    QTest.keyClick(native, Qt.Key_Escape)  # closes it without moving into it
    assert not picker.isVisible() and in_page()
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    QTest.keyClick(native, Qt.Key_W)
    assert picker.isVisible() and app.focusWidget() is picker.list
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Down)
    QTest.keyClick(native, Qt.Key_Return)
    assert raw()["senses"][0]["definition"] == "become less intense", raw()
    QTest.keyClick(native, Qt.Key_Escape)
    assert not picker.isVisible()
    wait_for(in_page, "for focus to return to the page")
finally:
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
