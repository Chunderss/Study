"""Real Qt tests for live notes, optional when the GUI extra isn't installed."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTextBrowser

from vocab.cli.app import App
from vocab.gui.components import NotesComponent
from vocab.gui.context import WorkspaceContext


@pytest.fixture(scope="module")
def qt():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def notes(qt, tmp_path):
    app = App(root=tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.storage.save_note("Book", "Chapter", "# Original\n\nA note.")
    ctx = WorkspaceContext(app)
    widget = NotesComponent(ctx)
    widget.resize(1000, 650)
    widget.show()
    widget.note_list.setCurrentRow(0)
    qt.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    qt.processEvents()


def test_typing_renders_markdown_without_saving_or_moving_cursor(notes, qt):
    preview = notes.findChild(QTextBrowser, "MarkdownPreview")
    assert preview is not None, "Notes needs a live rendered Markdown preview"
    assert preview.isVisible()
    notes.editor.setFocus()
    notes.editor.selectAll()
    QTest.keyClicks(notes.editor, "# Live heading")
    QTest.keyClick(notes.editor, Qt.Key_Return)
    QTest.keyClick(notes.editor, Qt.Key_Return)
    QTest.keyClicks(notes.editor, "**Bold** and *italic*")
    cursor = notes.editor.textCursor().position()
    QTest.qWait(250)
    assert preview.document().firstBlock().blockFormat().headingLevel() == 1
    assert "Live heading" in preview.toPlainText()
    assert "**Bold**" not in preview.toPlainText()
    assert preview.document().find("Bold").charFormat().fontWeight() >= 700
    assert preview.document().find("italic").charFormat().fontItalic()
    assert notes.editor.textCursor().position() == cursor
    assert notes.editor.hasFocus()
    assert notes.editor.document().isUndoAvailable()
    assert notes.app.storage.load_note("Book", "Chapter") == "# Original\n\nA note."
    assert "unsaved" in notes.dirty.text()
    raw = notes.editor.toPlainText()
    notes.save()
    assert notes.app.storage.load_note("Book", "Chapter") == raw
    QTest.keyClicks(notes.editor, "!")
    QTest.qWait(250)
    assert preview.toPlainText().endswith("italic!")
    notes.editor.undo()
    QTest.qWait(250)
    assert not preview.toPlainText().endswith("!")


def test_selection_switch_and_clear_render_immediately(notes, qt):
    preview = notes.findChild(QTextBrowser, "MarkdownPreview")
    assert preview.document().firstBlock().blockFormat().headingLevel() == 1
    assert "Original" in preview.toPlainText()
    notes.app.storage.save_note("Book", "Other", "## Second\n\n- Item")
    notes.refresh()
    notes.note_list.setCurrentItem(notes.note_list.findItems("Other", Qt.MatchExactly)[0])
    assert "Second" in preview.toPlainText()
    assert "Original" not in preview.toPlainText()
    notes.app.storage.delete_note("Book", "Other")
    notes.refresh()
    assert preview.toPlainText() == ""
    assert not notes.editor.isEnabled()


def test_preview_toggle_uses_real_tmux_keys_and_preserves_text(qt, tmp_path, monkeypatch):
    from vocab.gui.main_window import MainWindow
    # No dictionary setup/network is needed to exercise Notes UI.
    monkeypatch.setattr(MainWindow, "_bootstrap_dictionary", lambda self: None)
    win = MainWindow(root=tmp_path)
    win.app.cmd_create("Book"); win.app.cmd_use("Book")
    win.app.storage.save_note("Book", "One", "# Draft")
    win.show(); qt.processEvents()
    win.workspace.set_focused_component("notes")
    nc = win.workspace.focused.component
    nc.note_list.setCurrentRow(0)
    nc.editor.setFocus(); qt.processEvents()
    preview = nc.findChild(QTextBrowser, "MarkdownPreview")
    try:
        for visible in (False, True):
            QTest.keyClick(qt.focusWidget(), Qt.Key_B, Qt.ControlModifier)
            QTest.keyClick(qt.focusWidget(), Qt.Key_R)
            qt.processEvents()
            assert preview.isVisible() is visible
            assert nc.editor.hasFocus()
            assert nc.editor.toPlainText() == "# Draft"
        QTest.keyClick(nc.editor, Qt.Key_Tab)
        assert preview.hasFocus(), "Tab should reach the preview for keyboard scrolling"
        QTest.keyClick(preview, Qt.Key_B, Qt.ControlModifier)
        QTest.keyClick(preview, Qt.Key_R)
        qt.processEvents()
        assert not preview.isVisible()
        assert nc.editor.hasFocus(), "Hiding a focused preview should return to editing"
        assert any(row[0] == "r" and "preview" in row[2].lower() for row in win._keymap())
    finally:
        qt.removeEventFilter(win.hotkeys)
        win.close(); win.deleteLater(); qt.processEvents()


def test_preview_reflows_for_narrow_panes(notes, qt):
    notes.resize(1100, 650); qt.processEvents()
    split = notes.markdown.splitter
    assert split.orientation() == Qt.Horizontal
    notes.resize(500, 650); qt.processEvents()
    assert split.orientation() == Qt.Vertical, "Narrow panes need editor above preview"
    assert not notes.findChild(QTextBrowser, "MarkdownPreview").isHidden()
    assert notes.editor.height() > 50


def test_narrow_notes_moves_file_picker_above_editing_area(notes, qt):
    from PySide6.QtCore import QPoint
    notes.resize(384, 760); qt.processEvents()
    assert notes.editor.width() >= 300, "The fixed note list must not squeeze the editor"
    list_bottom = notes.note_list.mapTo(notes, notes.note_list.rect().bottomLeft()).y()
    editor_top = notes.editor.mapTo(notes, QPoint()).y()
    assert list_bottom < editor_top


def test_supported_formats_safe_links_and_preview_scroll(notes, qt):
    preview = notes.findChild(QTextBrowser, "MarkdownPreview")
    raw = ("# Guide\n\n- First\n- Second\n\n"
           "| Term | Meaning |\n| --- | --- |\n| ephemeral | brief |\n\n"
           "```python\nprint('hello')\n```\n\n[Link](https://example.com)\n\n"
           + "\n\n".join(f"Paragraph {i}" for i in range(100)))
    notes.editor.setPlainText(raw)
    QTest.qWait(250)
    doc = preview.document()
    assert doc.find("First").block().textList() is not None
    assert doc.find("ephemeral").currentTable() is not None
    assert "print('hello')" in preview.toPlainText()
    assert doc.find("Link").charFormat().isAnchor()
    assert not preview.openLinks() and not preview.openExternalLinks()
    bar = preview.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    before = bar.value()
    assert before > 0
    notes.editor.moveCursor(QTextCursor.End)
    notes.editor.insertPlainText("\n\nMore")
    QTest.qWait(250)
    assert bar.value() == before


def test_unrelated_refresh_does_not_erase_live_draft(notes, qt):
    notes.editor.setPlainText("# Unsaved draft")
    QTest.qWait(250)
    notes.ctx.changed.emit()  # e.g. adding a vocab word in another pane
    assert notes.editor.toPlainText() == "# Unsaved draft"
    assert "Unsaved draft" in notes.markdown.preview.toPlainText()
    assert "unsaved" in notes.dirty.text()
    # Same note name in another module MUST reload, not reuse this draft.
    notes.app.cmd_create("Other"); notes.app.cmd_use("Other")
    notes.app.storage.save_note("Other", "Chapter", "# Different book")
    notes.ctx.module_changed.emit("Other")
    assert notes.editor.toPlainText() == "# Different book"
    assert "Different book" in notes.markdown.preview.toPlainText()


def test_preview_never_loads_or_paints_image_files(notes, qt, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from vocab.gui.markdown_editor import MarkdownPreview
    preview = notes.findChild(QTextBrowser, "MarkdownPreview")
    image = tmp_path / "private.png"
    picture = QImage(300, 200, QImage.Format_RGB32)
    picture.fill(QColor(12, 200, 34))
    assert picture.save(str(image))
    requests = []
    load = MarkdownPreview.loadResource
    monkeypatch.setattr(MarkdownPreview, "loadResource",
                        lambda self, kind, url: requests.append(url.toString()) or load(self, kind, url))
    notes.editor.setPlainText(f"# Note\n\n![private]({image.as_uri()})\n\n"
                              "![unc](file://server/share/pixel.png)\n\n"
                              "![missing](file:///no/such/file.png)\n\n"
                              "![remote](https://example.com/pixel.png)\n")
    QTest.qWait(250)
    for _ in range(3):
        preview.viewport().repaint()
        qt.processEvents()
    shot = preview.viewport().grab().toImage()
    painted = sum(shot.pixelColor(x, y) == QColor(12, 200, 34)
                  for x in range(0, shot.width(), 4) for y in range(0, shot.height(), 4))
    assert painted == 0
    # Images are drawn as "[image: alt]" labels and never requested at all.
    assert requests == []
    assert notes.editor.toPlainText().count("](") == 4  # the Markdown source is unchanged


HIDPI = r'''
import sys
from pathlib import Path
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication
app = QApplication([])
from vocab.gui.markdown_editor import MarkdownEditor, MarkdownPreview
folder = Path(sys.argv[1])
for name in ("private.png", "private@2x.png"):
    picture = QImage(300, 200, QImage.Format_RGB32)
    picture.fill(QColor(12, 200, 34))
    assert picture.save(str(folder / name)), name  # the @2x sibling must exist
requests = []
load = MarkdownPreview.loadResource
MarkdownPreview.loadResource = lambda self, kind, url: requests.append(url.toString()) or load(self, kind, url)
editor = MarkdownEditor()
editor.resize(700, 500)
editor.show()
editor.editor.setPlainText(f"![private]({(folder / 'private.png').as_uri()})")
editor.render()
for _ in range(3):
    editor.preview.viewport().repaint()
    app.processEvents()
shot = editor.preview.viewport().grab().toImage()
painted = sum(shot.pixelColor(x, y) == QColor(12, 200, 34)
              for x in range(0, shot.width(), 4) for y in range(0, shot.height(), 4))
print("RESULT", app.devicePixelRatio(), len(requests), painted, flush=True)
'''


def test_preview_does_not_probe_hidpi_image_variants(tmp_path):
    # Qt's own image handler looks for "<name>@2x" files at scaling above 100%;
    # an existing sibling would then be requested even though the note never names it.
    import subprocess, sys
    from pathlib import Path
    env = dict(os.environ, QT_SCALE_FACTOR="2")
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = subprocess.run([sys.executable, "-c", HIDPI, str(tmp_path)], env=env,
                            cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    line = next((l for l in result.stdout.splitlines() if l.startswith("RESULT")), None)
    assert line, result.stdout + result.stderr
    _, ratio, requests, painted = line.split()
    assert float(ratio) == 2.0 and requests == "0" and painted == "0", line
