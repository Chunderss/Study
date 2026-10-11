"""EPUB reader panes keep their own place and zoom, also on one book, through
the real workspace."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]

SETUP = r'''
import json
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import EpubViewer

app = QApplication([])
root = Path(sys.argv[1])
windows = []
QMessageBox.question = lambda *args: QMessageBox.Discard

def open_window():
    window = MainWindow(root)
    window.resize(1200, 700)
    windows.append(window)
    return window

window = open_window()
window.app.cmd_create("Book")
window.app.cmd_use("Book")
window.ctx.module_changed.emit("Book")
path = window.app.paths.documents_dir("Book") / "book.epub"
page = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title></head><body>{1}</body></html>'
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/><item id="b" href="b.xhtml"/><item id="c" href="c.xhtml"/>'
                  '</manifest><spine><itemref idref="a"/><itemref idref="b"/><itemref idref="c"/></spine></package>')
    for name, title in (("a", "A"), ("b", "B"), ("c", "C"), ("notes", "Notes")):
        book.writestr(name + ".xhtml", page.format(title, f"<p>{title} chapter text</p>" * 400))

def readers(window):
    return [pane.component for pane in window.workspace._panes
            if isinstance(pane.component, EpubViewer)]

def wait_for(check, failure=lambda: "Timed out"):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(failure())

def settled(viewer, title):
    return (viewer._document_loaded and viewer._web.title() == title
            and not viewer._web.page().isLoading() and viewer._pending_scroll is None)

def wait_settled(viewer, title):
    wait_for(lambda: settled(viewer, title), lambda: f"title={viewer._web.title()!r}, "
             f"loaded={viewer._document_loaded}, pending={viewer._pending_scroll}")

def js(viewer, code):
    result = []
    viewer._run_js(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

def scroll_to(viewer, fraction):
    js(viewer, f"window.scrollTo(0, document.documentElement.scrollHeight * {fraction}); true;")
    wait_for(lambda: abs(viewer.source_location()["scroll"] - fraction) < 0.02,
             lambda: f"scroll={viewer.source_location()['scroll']}")

def where(viewer):
    """(chapter, href, scroll, zoom)."""
    position = viewer.source_location()
    return position["chapter"], position.get("href"), position["scroll"], viewer._zoom

def near(places, expected):
    """Whether places match, scroll within 2% of the chapter and zoom within 0.001."""
    return len(places) == len(expected) and all(
        place[:2] == want[:2] and abs(place[2] - want[2]) < 0.02 and abs(place[3] - want[3]) < 0.001
        for place, want in zip(places, expected))

def wheel(viewer):
    """Turn the mouse wheel over the page, which does not move keyboard focus."""
    target = viewer._web.focusProxy()
    center = target.rect().center()
    QApplication.sendEvent(target, QWheelEvent(
        QPointF(center), QPointF(target.mapToGlobal(center)), QPoint(), QPoint(0, -120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))

def press(viewer, label):
    QTest.mouseClick(next(b for b in viewer.findChildren(QPushButton) if b.text() == label),
                     Qt.LeftButton)

def saved():
    return window.app.storage.load_reading_pos("Book", "book.epub")

def close_all():
    for window in windows:
        window.close()
        window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''


def run_workspace(tmp_path, scenario):
    script = SETUP + "\ntry:\n" + textwrap.indent(scenario, "    ") + "\nfinally:\n    close_all()\n"
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags + " --disable-gpu --disable-smooth-scrolling"
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_epub_panes_reopen_at_their_own_place_and_zoom(tmp_path):
    run_workspace(tmp_path, r'''
window.show()
first = window._open_viewer(str(path))
wait_settled(first, "A")
first._go(1)
wait_settled(first, "B")
scroll_to(first, 0.5)
first._bump(first.ZOOM_STEP)
first._bump(first.ZOOM_STEP)
window.workspace.split_vertical()
second = window._open_viewer(str(path))
second.go_to({"chapter": 0, "href": "notes.xhtml", "scroll": 0.3})
wait_settled(second, "Notes")
scroll_to(second, 0.3)
second._bump(1 / second.ZOOM_STEP)
places = [where(viewer) for viewer in readers(window)]
assert near(places, [(1, None, 0.5, 1.653), (0, "notes.xhtml", 0.3, 1.087)]), places
expected = places
assert window.close()

reopened = open_window()
assert not reopened._session_warning
reopened.show()
wait_settled(readers(reopened)[0], "B")
wait_settled(readers(reopened)[1], "Notes")
places = [where(viewer) for viewer in readers(reopened)]
assert near(places, expected), (places, expected)
assert reopened.close()

# An older workspace file has no positions, and damaged parts are ignored:
# the book's saved place or zoom applies instead.
window.app.storage.save_reading_pos("Book", "book.epub", {"chapter": 2, "scroll": 0.0, "zoom": 1.4})
cases = [
    (None, (2, None, 0.0, 1.4)),
    ([1], (2, None, 0.0, 1.4)),
    ({"chapter": "x", "zoom": "y"}, (2, None, 0.0, 1.4)),
    ({"chapter": True, "scroll": 0.5, "zoom": True}, (2, None, 0.0, 1.4)),
    ({"chapter": -1, "zoom": float("nan")}, (2, None, 0.0, 1.4)),
    ({"chapter": 1, "scroll": "x", "zoom": 10 ** 400}, (2, None, 0.0, 1.4)),
    ({"chapter": 1, "scroll": 0.25, "href": 5, "zoom": 1.1}, (2, None, 0.0, 1.1)),
    ({"chapter": 0, "scroll": 0.25, "zoom": "y"}, (0, None, 0.25, 1.4)),
    ({"zoom": 1.1}, (2, None, 0.0, 1.1)),
]
panes = []
for order, (position, _expected) in enumerate(cases):
    pane = {"component": "viewer", "module": "Book", "filename": "book.epub"}
    if position is not None:
        pane["position"] = position
    panes.append({"pane": pane, "order": order})
(root / "workspace.json").write_text(json.dumps({
    "schema": 1, "module": "Book", "sidebar": True,
    "workspace": {"tree": {"direction": "h", "sizes": [], "children": panes},
                  "focused": 0, "zoomed": False}}), encoding="utf-8")
reopened = open_window()
assert reopened._session_warning == ""
places = [where(viewer) for viewer in readers(reopened)]
assert near(places, [expected for _, expected in cases]), places

# A saved footnote that is not a file name is ignored, not an error.
window.app.storage.save_reading_pos("Book", "book.epub", {"chapter": 1, "scroll": 0.0, "href": 5})
viewer = EpubViewer(str(path), reopened.ctx, module="Book")
try:
    assert near([where(viewer)], [(1, None, 0.0, 1.25)]), where(viewer)
finally:
    viewer.close()
    viewer.deleteLater()
''')


def test_the_epub_reader_scrolled_or_zoomed_last_decides_the_saved_place(tmp_path):
    run_workspace(tmp_path, r'''
window.show()
first = window._open_viewer(str(path))
wait_settled(first, "A")
window.workspace.split_vertical()
second = window._open_viewer(str(path))
second._jump(2)
wait_settled(second, "C")
scroll_to(second, 0.4)
focus = QApplication.focusWidget()
wheel(first)  # over the first pane, while the second keeps keyboard focus
wait_for(lambda: first.source_location()["scroll"] > 0, lambda: first.source_location())
assert QApplication.focusWidget() is focus
expected = first.reading_position()
assert window.close()
assert saved() == expected, (saved(), expected)

reopened = open_window()
reopened.show()
first, second = readers(reopened)
wait_settled(first, "A")
wait_settled(second, "C")
press(second, "A+")
expected = second.reading_position()
assert reopened.close()
assert saved() == expected, (saved(), expected)
''')
