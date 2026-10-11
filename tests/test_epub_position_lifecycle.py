"""An EPUB reader saves only places it has: never the outgoing chapter's
scroll with the next chapter, and never a default after a failed load."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

SETUP = r'''
import sys
import zipfile
from PySide6.QtCore import QEvent, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer

app = QApplication([])
data = App(sys.argv[1])
data.cmd_create("Book")
data.cmd_use("Book")
ctx = WorkspaceContext(data)
path = data.paths.documents_dir("Book") / "book.epub"
page = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title></head><body>{1}</body></html>'
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest>'
                  '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
    book.writestr("a.xhtml", page.format("A", "<p>First chapter text</p>" * 400))
    book.writestr("b.xhtml", page.format("B", "<p>Second chapter text</p>" * 400))
viewers = []

def open_viewer(cls=EpubViewer):
    viewer = cls(str(path), ctx)
    viewer.resize(900, 600)
    viewer.show()
    viewers.append(viewer)
    return viewer

def wait_for(check, viewer=None):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError("Timed out" + (
        f": title={viewer._web.title()}, error={viewer._error.text()}, loaded={viewer._document_loaded}, "
        f"loading={viewer._web.page().isLoading()}, pending={viewer._pending_scroll}" if viewer else ""))

def settled(viewer, title):
    return (viewer._document_loaded and viewer._web.title() == title
            and not viewer._web.page().isLoading() and viewer._pending_scroll is None)

def js(viewer, code):
    result = []
    viewer._run_js(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

def saved():
    return data.storage.load_reading_pos("Book", "book.epub")

def shutdown():
    for viewer in viewers:
        viewer.close()
        viewer.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''


def run_reader(tmp_path, scenario):
    script = SETUP + "\ntry:\n" + textwrap.indent(scenario, "    ") + "\nfinally:\n    shutdown()\n"
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " --disable-gpu"
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_new_chapter_is_never_saved_with_the_old_scroll(tmp_path):
    run_reader(tmp_path, r'''
viewer = open_viewer()
wait_for(lambda: settled(viewer, "A"), viewer)
js(viewer, "window.scrollTo(0, document.documentElement.scrollHeight * .65); true;")
wait_for(lambda: viewer.source_location()["scroll"] > .6)
viewer._go(1)
# Saved before chapter B finished loading: B at its top, not A's 65%.
assert viewer.source_location() == {"kind": "epub", "filename": "book.epub", "chapter": 1, "scroll": 0.0}
viewer.save_position()
assert saved()["chapter"] == 1 and saved()["scroll"] == 0.0, saved()
wait_for(lambda: settled(viewer, "B"), viewer)
assert viewer.source_location()["scroll"] == 0.0

# A source jump to the top of the current chapter is a real target, not "none".
js(viewer, "window.scrollTo(0, document.documentElement.scrollHeight * .5); true;")
wait_for(lambda: viewer.source_location()["scroll"] > .4)
viewer.go_to({"kind": "epub", "filename": "book.epub", "chapter": 1, "scroll": 0.0})
assert viewer.source_location()["scroll"] == 0.0
viewer.save_position()
assert saved()["scroll"] == 0.0, saved()
wait_for(lambda: settled(viewer, "B"), viewer)
assert js(viewer, "window.scrollY") == 0
assert viewer.source_location()["scroll"] == 0.0
''')


def test_a_failed_first_load_keeps_the_saved_place_until_a_chapter_loads(tmp_path):
    run_reader(tmp_path, r'''
before = {"chapter": 1, "scroll": 0.6, "zoom": 1.5, "href": "missing.xhtml"}
data.storage.save_reading_pos("Book", "book.epub", before)
viewer = open_viewer()
assert not viewer._error.isHidden() and "Could not read chapter" in viewer._error.text()
viewer.save_position()
viewer.on_replaced()
assert saved() == before
# A chapter that loads later is a real place again.
viewer = open_viewer()
viewer._go(1)
wait_for(lambda: settled(viewer, "B"), viewer)
viewer.save_position()
assert saved() == {"chapter": 1, "scroll": 0.0, "zoom": 1.5}, saved()


class Failing(EpubViewer):
    """The browser cannot load chapter B while it is broken."""
    broken = True

    def _chapter_url(self, i, fragment="", href=None):
        url = super()._chapter_url(i, fragment, href)
        if i == 1 and self.broken:
            real = str(Path(url.toLocalFile()).resolve())
            missing = Path(real).with_name(".vocab-missing.html")
            self._source_paths[str(missing)] = self._source_paths[real]
            url = QUrl.fromLocalFile(str(missing))
        return url

from pathlib import Path
before = {"chapter": 1, "scroll": 0.6, "zoom": 1.5}
data.storage.save_reading_pos("Book", "book.epub", before)
viewer = open_viewer(Failing)
wait_for(lambda: not viewer._error.isHidden(), viewer)
viewer.save_position()
assert saved() == before, saved()
viewer.broken = False
viewer._go(-1)
wait_for(lambda: settled(viewer, "A"), viewer)
viewer.save_position()
assert saved() == {"chapter": 0, "scroll": 0.0, "zoom": 1.5}, saved()
''')


def test_a_link_into_the_same_chapter_keeps_the_live_position(tmp_path):
    run_reader(tmp_path, r'''
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/></manifest>'
                  '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
    book.writestr("a.xhtml", page.format("A", "<p>Text</p>" * 300 + '<a id="ref" href="a.xhtml#fn1">1</a>'
                                         '<a id="self" href="a.xhtml">top</a><p id="fn1">The note</p>'))
    book.writestr("b.xhtml", page.format("B", "<p>Second chapter text</p>" * 400))
viewer = open_viewer()
wait_for(lambda: settled(viewer, "A"), viewer)
js(viewer, "window.scrollTo(0, document.documentElement.scrollHeight); true;")
wait_for(lambda: viewer.source_location()["scroll"] > .9)
bottom = viewer.source_location()["scroll"]
js(viewer, 'document.getElementById("ref").click(); true;')  # its target is already in view
QTest.qWait(300)
assert viewer._document_loaded and viewer.source_location()["scroll"] == bottom
viewer.save_position()
assert saved()["scroll"] == bottom, saved()
''')


def test_returning_before_the_next_chapter_appears_keeps_the_reader_working(tmp_path):
    run_reader(tmp_path, r'''
viewer = open_viewer()
wait_for(lambda: settled(viewer, "A"), viewer)
js(viewer, "window.scrollTo(0, document.documentElement.scrollHeight * .5); true;")
wait_for(lambda: viewer.source_location()["scroll"] > .4)
viewer._go(1)
viewer._go(-1)  # before chapter B could load
QTest.qWait(500)
assert viewer._document_loaded and viewer._web.title() == "A" and viewer._idx == 0
js(viewer, "window.scrollTo(0, document.documentElement.scrollHeight * .7); true;")
wait_for(lambda: viewer.source_location()["scroll"] > .6)
captures = []
ctx.capture_requested.connect(lambda *args: captures.append(args))
js(viewer, 'var r = document.createRange(); r.selectNodeContents(document.body.lastElementChild); '
           'getSelection().removeAllRanges(); getSelection().addRange(r); true;')
viewer._capture()
wait_for(lambda: bool(captures))
''')


@pytest.mark.parametrize("href", ["#", "a.xhtml#", "a.xhtml"])
def test_explicit_links_to_the_current_chapter_top(tmp_path, href):
    run_reader(tmp_path, r'''
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.xhtml", page.format("A", "<p>Chapter text</p>" * 400 +
                  '<a id="self" href="%s">Return to top</a>'))
viewer = open_viewer()
wait_for(lambda: settled(viewer, "A"), viewer)
js(viewer, "window.scrollTo(0, document.documentElement.scrollHeight * .8); true;")
wait_for(lambda: viewer.source_location()["scroll"] > .7, viewer)
js(viewer, 'document.getElementById("self").click(); true;')
wait_for(lambda: js(viewer, "window.scrollY") == 0, viewer)
wait_for(lambda: settled(viewer, "A"), viewer)
viewer.save_position()
assert saved()["scroll"] == 0.0, saved()
''' % href)


def test_restoring_the_current_position_keeps_tracking_reflow(tmp_path):
    run_reader(tmp_path, r'''
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.xhtml", page.format("A", '<style>body { overflow-anchor: none; }</style>' +
                  ('<p>' + 'A sentence that wraps over several lines in a narrow window. ' * 10 + '</p>') * 80))
viewer = open_viewer()
viewer.resize(550, 650)
wait_for(lambda: settled(viewer, "A"), viewer)
js(viewer, 'window.scrollTo(0, document.scrollingElement.scrollHeight * .2); true;')
wait_for(lambda: viewer.source_location()["scroll"] > .19, viewer)
fraction = js(viewer, 'window.scrollY / document.scrollingElement.scrollHeight')
viewer.go_to({"chapter": 0, "scroll": fraction})
wait_for(lambda: settled(viewer, "A"), viewer)
viewer.resize(1000, 650)

def renderer_fraction():
    return js(viewer, 'window.scrollY / document.scrollingElement.scrollHeight')

wait_for(lambda: renderer_fraction() > fraction + .05, viewer)
actual = renderer_fraction()
# Wait for Qt's cache independently: a late cache is a different scenario.
wait_for(lambda: abs(viewer._web.page().scrollPosition().y() /
                    viewer._web.page().contentsSize().height() - actual) < .002, viewer)
assert abs(viewer.source_location()["scroll"] - actual) < .002, (viewer.source_location(), actual)
viewer.save_position()
assert abs(saved()["scroll"] - actual) < .002, saved()
''')
