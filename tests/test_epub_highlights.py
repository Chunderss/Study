"""Saved learning captures remain highlighted in the correct EPUB passage."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")


BROWSER_SETUP = r"""
import json
import sys
import zipfile
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.core.learning import LearningStore
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
data = App(sys.argv[1])
quote = "Café 😀 shared passage"
body = ('<p id="first">First 🍀 Café <em>😀 shared</em> passage after first.</p>'
        '<div style="height:1800px"></div>'
        '<p id="second">Second 🌙 Café <em>😀 shared</em> passage after second.</p>'
        '<p id="legacy">Unique legacy sentence.</p>'
        '<p id="footnote">Unique footnote.</p>' + '<p>More reading text.</p>' * 120)
for module in ("Book", "Other"):
    data.cmd_create(module)
    for filename in ("book.epub", "other.epub"):
        path = data.paths.documents_dir(module) / filename
        with zipfile.ZipFile(path, "w") as book:
            book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="Text/one.xhtml"/><item id="two" href="Text/two.xhtml"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
            for title, name in (("one", "one.xhtml"), ("two", "two.xhtml"), ("footnote", "notes #part.xhtml")):
                book.writestr("Text/" + name, '<html><head><title>' + title + '</title></head><body>' + body + '</body></html>')
data.cmd_use("Book")
ctx = WorkspaceContext(data)
store = LearningStore(data.storage)
views = []

def wait_for(check, failure=lambda: "Browser condition timed out"):
    for _ in range(500):
        if check():
            return
        QTest.qWait(10)
    raise AssertionError(failure())

def js(viewer, code):
    result = []
    viewer._web.page().runJavaScript(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

def loaded(viewer, title):
    return viewer._web.title() == title and not viewer._web.page().isLoading()

def open_viewer(module="Book", filename="book.epub", source=None, show=True):
    viewer = EpubViewer(str(data.paths.documents_dir(module) / filename), ctx,
                        module=module, source=source)
    views.append(viewer)
    viewer.resize(900, 600)
    if show:
        viewer.show()
    wait_for(lambda: loaded(viewer, "one"))
    return viewer

def marks(viewer):
    return json.loads(js(viewer, '''JSON.stringify(Array.from(
        CSS.highlights.get('vocab-study-saved') || [], range => ({
            text: range.toString(),
            paragraph: range.startContainer.parentElement.closest('p').id
        })))'''))

def expect_marks(viewer, expected):
    wait_for(lambda: sorted((m["paragraph"], m["text"]) for m in marks(viewer)) == sorted(expected))

def capture_source(viewer, paragraph="second"):
    js(viewer, '''(() => {
        const p = document.getElementById(''' + json.dumps(paragraph) + ''');
        const range = document.createRange();
        range.setStart(p.firstChild, p.firstChild.textContent.indexOf('Café'));
        range.setEnd(p.lastChild, ' passage'.length);
        const selection = window.getSelection();
        selection.removeAllRanges(); selection.addRange(range);
        return true;
    })()''')
    wait_for(lambda: viewer._web.selectedText() == quote)
    captures = []
    receive = lambda *args: captures.append(args)
    ctx.capture_requested.connect(receive)
    try:
        viewer._capture()
        wait_for(lambda: bool(captures))
    finally:
        ctx.capture_requested.disconnect(receive)
    assert len(captures) == 1
    module, selected, source = captures[0]
    assert module == viewer._module and selected == quote
    return source

def save_capture(module, text, source):
    item = store.save_capture(module, kind="concept", prompt="Explain this passage",
                              quote=text, source=source)
    ctx.learning_changed.emit()
    return item

def shutdown():
    for viewer in reversed(views):
        viewer.close()
        viewer.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
"""


def run_browser(tmp_path, scenario):
    script = BROWSER_SETUP + "\ntry:\n" + textwrap.indent(scenario, "    ") + "\nfinally:\n    shutdown()\n"
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags + " --disable-gpu --disable-smooth-scrolling"
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_capture_anchor_persists_unicode_inline_selection_and_correct_occurrence(tmp_path):
    run_browser(tmp_path, '''
viewer = open_viewer()
source = capture_source(viewer)
anchor = source["anchor"]
assert anchor["version"] == 1 and anchor["exact"] == quote
assert anchor["end"] - anchor["start"] == len(quote.encode("utf-16-le")) // 2
assert anchor["prefix"] and anchor["suffix"]
# Selection and opening a capture alone must not create a saved highlight.
expect_marks(viewer, [])
item = save_capture("Book", quote, source)
assert store.load("Book")[item["id"]]["source"]["anchor"] == anchor
expect_marks(viewer, [("second", quote)])
assert viewer._web.selectedText() == quote
assert js(viewer, "document.querySelectorAll('p em').length") == 2

viewer.save_position()
reopened = open_viewer()
expect_marks(reopened, [("second", quote)])
assert js(reopened, "document.getElementById('first').textContent.includes('Café')")
''')


def test_edit_refresh_and_delete_preserve_selection_scroll_and_original_passage(tmp_path):
    run_browser(tmp_path, '''
viewer = open_viewer()
source = capture_source(viewer)
item = save_capture("Book", quote, source)
expect_marks(viewer, [("second", quote)])
js(viewer, "window.scrollTo(0, 900); true;")
wait_for(lambda: viewer._web.page().scrollPosition().y() > 0)
before = js(viewer, "window.scrollY")

item = store.save_capture("Book", kind="concept", prompt="A revised question",
                          quote="My shortened excerpt", explanation="My explanation",
                          source=source, item_id=item["id"], revision=item["revision"])
for _ in range(3):
    ctx.learning_changed.emit()
expect_marks(viewer, [("second", quote)])
assert viewer._web.selectedText() == quote
assert abs(js(viewer, "window.scrollY") - before) <= 1
assert store.load("Book")[item["id"]]["quote"] == "My shortened excerpt"

store.delete("Book", item["id"], item["revision"])
ctx.learning_changed.emit()
expect_marks(viewer, [])
assert viewer._web.selectedText() == quote
assert abs(js(viewer, "window.scrollY") - before) <= 1
''')


def test_highlights_are_isolated_by_module_file_chapter_and_nonspine_href(tmp_path):
    run_browser(tmp_path, '''
viewer = open_viewer()
source = capture_source(viewer)
save_capture("Book", quote, source)
expect_marks(viewer, [("second", quote)])

# An open reader stays attached to its original module after sidebar changes.
data.cmd_use("Other")
other_module = open_viewer("Other")
expect_marks(other_module, [])
other_source = capture_source(other_module, "first")
save_capture("Other", quote, other_source)
expect_marks(other_module, [("first", quote)])
expect_marks(viewer, [("second", quote)])
other_file = open_viewer("Book", "other.epub")
expect_marks(other_file, [])

# Legacy captures have no anchor: a unique quote can be restored, while a
# duplicate quote must not select an arbitrary occurrence.
legacy = {key: value for key, value in source.items() if key != "anchor"}
save_capture("Book", "Unique legacy sentence.", legacy)
save_capture("Book", quote, legacy)
expect_marks(viewer, [("second", quote), ("legacy", "Unique legacy sentence.")])
footnote = dict(legacy, href="Text/notes%20%23part.xhtml")
save_capture("Book", "Unique footnote.", footnote)
expect_marks(viewer, [("second", quote), ("legacy", "Unique legacy sentence.")])

viewer.go_to({"chapter": 1, "scroll": 0})
wait_for(lambda: loaded(viewer, "two"))
expect_marks(viewer, [])
viewer.go_to({"chapter": 0, "href": footnote["href"], "scroll": 0})
wait_for(lambda: loaded(viewer, "footnote"))
expect_marks(viewer, [("footnote", "Unique footnote.")])
viewer.go_to({"chapter": 0, "scroll": 0})
wait_for(lambda: loaded(viewer, "one"))
expect_marks(viewer, [("second", quote), ("legacy", "Unique legacy sentence.")])
''')


@pytest.mark.parametrize("hidden", [False, True])
def test_source_jump_focuses_exact_passage_over_saved_scroll(tmp_path, hidden):
    run_browser(tmp_path, f"hidden = {hidden!r}\n" + '''
viewer = open_viewer()
source = capture_source(viewer)
item = save_capture("Book", quote, source)
expect_marks(viewer, [("second", quote)])
data.storage.save_reading_pos("Book", "book.epub", {"chapter": 0, "scroll": 0.95})
jump = dict(source, scroll=0.95, capture_id=item["id"])
reopened = open_viewer(source=jump, show=not hidden)
if hidden:
    assert not reopened._web.isVisible()
    assert reopened._pending_highlight == item["id"]
    reopened.show()
expect_marks(reopened, [("second", quote)])

def focused_passage():
    return js(reopened, """(() => {
        const ranges = Array.from(CSS.highlights.get('vocab-study-active') || []);
        if (ranges.length !== 1) return false;
        const range = ranges[0], rect = range.getBoundingClientRect();
        return range.startContainer.parentElement.closest('p').id === 'second' &&
            rect.top >= 0 && rect.bottom <= innerHeight && window.scrollY > 1000;
    })()""")

wait_for(focused_passage, lambda: js(reopened, """JSON.stringify({
    scroll: scrollY, width: innerWidth, height: innerHeight,
    passage: document.getElementById('second').getBoundingClientRect().toJSON()
})"""))
# A delayed fractional restore must not subsequently move away from the anchor.
for _ in range(20):
    QTest.qWait(20)
    assert focused_passage()
assert reopened._pending_scroll == 0
assert reopened._pending_highlight is None
''')
