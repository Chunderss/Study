"""Explicit reader input must take precedence over pending scroll restoration."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QWidget
from shiboken6 import delete

from .test_markdown_preview import qt
from vocab.gui.viewers import EpubViewer


@pytest.mark.parametrize("target, kind, key, modifiers, loaded, cancelled", [
    ("foreign", QEvent.KeyPress, Qt.Key_End, Qt.NoModifier, True, False),
    ("child", QEvent.KeyPress, Qt.Key_A, Qt.NoModifier, True, False),
    ("child", QEvent.KeyPress, Qt.Key_End, Qt.NoModifier, True, True),
    ("web", QEvent.Wheel, None, Qt.NoModifier, True, True),
    ("child", QEvent.TouchBegin, None, Qt.NoModifier, True, True),
    ("child", QEvent.MouseButtonPress, None, Qt.NoModifier, True, True),
    ("child", QEvent.KeyPress, Qt.Key_Down, Qt.ControlModifier | Qt.ShiftModifier, True, False),
    ("child", QEvent.KeyPress, Qt.Key_Left, Qt.AltModifier, True, False),
    ("child", QEvent.KeyPress, Qt.Key_End, Qt.MetaModifier, True, False),
    ("child", QEvent.KeyPress, Qt.Key_Home, Qt.ControlModifier, True, True),
    ("child", QEvent.KeyPress, Qt.Key_End, Qt.ControlModifier | Qt.ShiftModifier, True, True),
    ("child", QEvent.KeyPress, Qt.Key_Home, Qt.ControlModifier | Qt.AltModifier, True, False),
    ("child", QEvent.KeyPress, Qt.Key_PageDown, Qt.ShiftModifier, True, True),
    ("child", QEvent.KeyPress, Qt.Key_End, Qt.NoModifier, False, False),
    ("web", QEvent.Wheel, None, Qt.NoModifier, False, False),
    ("child", QEvent.MouseButtonPress, None, Qt.NoModifier, False, False),
])
def test_scroll_cancellation_is_scoped_to_reader_input(qt, target, kind, key, modifiers, loaded, cancelled):
    viewer = EpubViewer.__new__(EpubViewer)
    QWidget.__init__(viewer)
    viewer._pending_scroll = 0.37
    viewer._document_loaded = loaded
    viewer._web = QWidget(viewer)
    child = QWidget(viewer._web)
    foreign = QWidget()
    event = QKeyEvent(kind, key, modifiers) if key is not None else QEvent(kind)
    try:
        watched = {"foreign": foreign, "child": child, "web": viewer._web}[target]
        assert viewer.eventFilter(watched, event) is False
        assert viewer._pending_scroll == (0.0 if cancelled else 0.37)
    finally:
        delete(foreign)
        delete(viewer)


@pytest.mark.parametrize("scroll_input", ["end_key", "wheel"])
def test_manual_scroll_cancels_restore_without_blocking_next_navigation(tmp_path, scroll_input):
    script = r'''
import json
import sys
import zipfile
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer

app = QApplication([])
data = App(sys.argv[1])
data.cmd_create("Book")
data.cmd_use("Book")
path = data.paths.documents_dir("Book") / "scroll.epub"
sections = ''.join(f'<section>Passage {i}</section>' for i in range(90))
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="start" href="start.html"/><item id="snap" href="snap.html"/><item id="plain" href="plain.html"/></manifest><spine><itemref idref="start"/><itemref idref="snap"/><itemref idref="plain"/></spine></package>')
    book.writestr("start.html", '<html><head><title>start</title></head><body>Start</body></html>')
    # Targets spaced more closely than the viewport force an unreachable snap
    # position on both native Windows rendering and Linux's offscreen platform.
    book.writestr("snap.html", '<html><head><title>snap</title><style>html {scroll-snap-type:y mandatory;scroll-behavior:auto} body {margin:0} section {height:100px;scroll-snap-align:start}</style></head><body>' + sections + '</body></html>')
    book.writestr("plain.html", '<html><head><title>plain</title><style>body {margin:0} section {height:100px}</style></head><body>' + sections + '</body></html>')

ctx = WorkspaceContext(data)
viewer = EpubViewer(str(path), ctx)
viewer.resize(900, 600)
viewer.show()
attempts = []
restore = viewer._restore_scroll

def counted_restore(generation, attempt=0):
    attempts.append((generation, attempt))
    restore(generation, attempt)

viewer._restore_scroll = counted_restore

def wait_for(check):
    for _ in range(500):
        if check():
            return
        QTest.qWait(10)
    raise AssertionError(f"Timed out: title={viewer._web.title()}, pending={viewer._pending_scroll}, attempts={attempts}")

def js(code):
    result = []
    viewer._web.page().runJavaScript(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

try:
    wait_for(lambda: viewer._web.title() == "start" and not viewer._web.page().isLoading())
    viewer.go_to({"chapter": 1, "scroll": 0.37})
    wait_for(lambda: len(attempts) >= 2)
    before = js('window.scrollY')
    assert viewer._pending_scroll == 0.37, attempts
    assert 0 < before < 4000, before

    viewer._web.setFocus()
    target = viewer._web.focusProxy()
    assert target is not None
    if sys.argv[2] == "end_key":
        QTest.keyClick(target, Qt.Key_End)
    else:
        center = target.rect().center()
        wheel = QWheelEvent(QPointF(center), QPointF(target.mapToGlobal(center)),
                            QPoint(), QPoint(0, -1200), Qt.NoButton,
                            Qt.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(target, wheel)

    # Cancellation happens on input, before an old renderer callback or retry
    # can reapply the saved position. The input must still reach Chromium.
    assert viewer._pending_scroll == 0, (sys.argv[2], attempts)
    # Wheel distance depends on the platform; one 100px snap step is enough.
    minimum_movement = 50 if sys.argv[2] == "wheel" else 500
    def cached_css_scroll():
        return viewer._web.page().scrollPosition().y() / viewer._web.zoomFactor()
    wait_for(lambda: cached_css_scroll() > before + minimum_movement)
    # Check throughout the retry window: a snap-back must not be hidden by a
    # later wheel update or by comparing Qt pixels with Chromium's CSS pixels.
    for _ in range(40):
        QTest.qWait(20)
        position = cached_css_scroll()
        assert position > before + minimum_movement, (sys.argv[2], before, position, attempts)
    after = js('window.scrollY')
    assert after > before + minimum_movement, (sys.argv[2], before, after, attempts)
    if sys.argv[2] == "end_key":
        bottom = js('document.scrollingElement.scrollHeight - innerHeight')
        assert abs(after - bottom) <= 2, (after, bottom)

    # Cancelling this request must not suppress restoration on a later chapter.
    viewer.go_to({"chapter": 2, "scroll": 0.25})
    wait_for(lambda: viewer._web.title() == "plain" and not viewer._web.page().isLoading())
    wait_for(lambda: viewer._pending_scroll == 0)
    position = json.loads(js('JSON.stringify({y:scrollY,height:document.scrollingElement.scrollHeight})'))
    assert abs(position['y'] / position['height'] - 0.25) < 0.001, position
    assert viewer.source_location()["chapter"] == 2
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
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags + " --disable-gpu --disable-smooth-scrolling"
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path), scroll_input],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


def test_wheel_before_first_load_preserves_saved_scroll(tmp_path):
    script = r'''
import json
import sys
import zipfile
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.cli.app import App
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubViewer

app = QApplication([])
data = App(sys.argv[1])
data.cmd_create("Book")
data.cmd_use("Book")
path = data.paths.documents_dir("Book") / "saved.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="chapter" href="chapter.html"/></manifest><spine><itemref idref="chapter"/></spine></package>')
    book.writestr("chapter.html", '<html><head><title>saved</title></head><body>' + '<p>Reading passage</p>' * 400 + '</body></html>')
data.storage.save_reading_pos("Book", path.name, {"chapter": 0, "scroll": 0.37})
viewer = EpubViewer(str(path), WorkspaceContext(data))
viewer.resize(900, 600)
viewer.show()

def wait_for(check):
    for _ in range(500):
        if check():
            return
        QTest.qWait(10)
    raise AssertionError(f"Timed out: loaded={viewer._document_loaded}, pending={viewer._pending_scroll}")

try:
    # Deliver real input before processing any browser loading callbacks.
    assert not viewer._document_loaded
    assert viewer._pending_scroll == 0.37
    target = viewer._web.focusProxy()
    assert target is not None
    center = target.rect().center()
    wheel = QWheelEvent(QPointF(center), QPointF(target.mapToGlobal(center)),
                        QPoint(), QPoint(0, -1200), Qt.NoButton,
                        Qt.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(target, wheel)
    assert viewer._pending_scroll == 0.37

    wait_for(lambda: viewer._document_loaded and viewer._pending_scroll == 0)
    positions = []
    viewer._web.page().runJavaScript(
        'JSON.stringify({y:scrollY,height:document.scrollingElement.scrollHeight})',
        positions.append)
    wait_for(lambda: bool(positions))
    position = json.loads(positions[0])
    assert abs(position["y"] / position["height"] - 0.37) < 0.001, position
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
    flags = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = flags + " --disable-gpu --disable-smooth-scrolling"
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
