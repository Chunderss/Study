"""Workspace shortcuts remain usable inside a real Chromium reader."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytest.importorskip("PySide6")


def test_browser_hotkeys_follow_focus_across_chapters_and_reload(tmp_path):
    script = r'''
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.cli.app import App
from vocab.gui.main_window import MainWindow

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
root = Path(sys.argv[1])
data = App(root)
data.cmd_create("Book")
path = data.paths.documents_dir("Book") / "book.epub"
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="one" href="one.html"/><item id="two" href="two.html"/></manifest><spine><itemref idref="one"/><itemref idref="two"/></spine></package>')
    for name in ("one", "two"):
        book.writestr(name + ".html", f'<html><head><title>{name}</title></head><body><input id="answer"/><p>Chapter {name}</p></body></html>')

window = MainWindow(root)
window.app.cmd_use("Book")
window.ctx.module_changed.emit("Book")
window.show()
viewer = window._open_viewer(str(path))
window.workspace.split_vertical()

def wait_for(check):
    for _ in range(500):
        if check():
            return
        QTest.qWait(20)
    raise AssertionError(f"Timed out: title={viewer._web.title()}, focus={app.focusWidget()}, armed={window.hotkeys._armed}")

def js(code):
    result = []
    viewer._web.page().runJavaScript(code, result.append)
    wait_for(lambda: bool(result))
    return result[0]

def loaded(title):
    return viewer._web.title() == title and not viewer._web.page().isLoading()

def verify_reader_shortcuts():
    window.activateWindow()
    viewer.focus_default()
    wait_for(lambda: window.workspace.focused.component is viewer)
    js('document.getElementById("answer").focus(); true;')

    # Platform input enters through QWindow; Ctrl+B release must stay armed.
    QTest.keyClick(window.windowHandle(), Qt.Key_B, Qt.ControlModifier)
    assert window.hotkeys._armed
    QTest.keyClick(window.windowHandle(), Qt.Key_Z)
    assert window.workspace.is_zoomed
    assert not window.hotkeys._armed

    # Chromium may replace its input widget when navigating or reloading.
    browser = viewer._web.focusProxy()
    assert browser is not None
    QTest.keyClick(browser, Qt.Key_B, Qt.ControlModifier)
    assert window.hotkeys._armed
    QTest.keyClick(browser, Qt.Key_Z)
    assert not window.workspace.is_zoomed
    assert not window.hotkeys._armed
    assert len(window.workspace._panes) == 2
    assert js('document.getElementById("answer").value') == ""

try:
    wait_for(lambda: loaded("one"))
    verify_reader_shortcuts()
    viewer._go(1)
    wait_for(lambda: loaded("two"))
    verify_reader_shortcuts()
    finished = []
    viewer._web.loadFinished.connect(finished.append)
    viewer._web.reload()
    wait_for(lambda: finished and loaded("two"))
    assert finished[-1]
    verify_reader_shortcuts()
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
