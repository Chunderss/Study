"""Exercise the styled Notes UI and measure preview geometry before screenshots."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import json
import tempfile
from pathlib import Path
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from vocab.gui import theme
from vocab.gui.main_window import MainWindow

app = QApplication([])
# The offscreen Windows plugin can expose zero system font families. Load real
# installed fonts for screenshots; this is test setup, not fake rendered text.
from PySide6.QtGui import QFontDatabase
if not QFontDatabase.families() and os.name == "nt":
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for filename in ("consola.ttf", "consolab.ttf", "consolai.ttf", "segoeui.ttf"):
        font = font_dir / filename
        if font.is_file():
            assert QFontDatabase.addApplicationFont(str(font)) >= 0
print("Screenshot font families:", QFontDatabase.families())
app.setStyleSheet(theme.stylesheet())
output = Path(tempfile.mkdtemp(prefix="vocab_markdown_qa_"))
win = MainWindow(root=output / "home")
win.app.cmd_create("Demo"); win.app.cmd_use("Demo")
source = """# Reading notes

An **ephemeral** moment is *short-lived*.

## Questions for later
- How does the narrator change?
- Which details repeat?

> Compare this passage with the next chapter.

| Word | Meaning |
| --- | --- |
| verdant | green with vegetation |
| ephemeral | brief |

```python
review('ephemeral')
```
"""
win.app.storage.save_note("Demo", "Chapter notes", source)
win._refresh_modules()
win.show()
win.workspace.set_focused_component("notes")
nc = win.workspace.focused.component
nc.note_list.setCurrentRow(0)
measurements = []
for width in (1200, 800):
    if width == 800:
        # Real tiled layout: keep Notes in one pane and put Vocab beside it.
        win.workspace.split_vertical()
        win.workspace.set_focused_component("vocab")
    win.resize(width, 760)
    QTest.qWait(250)
    md = nc.markdown
    editor = QRect(md.editor.mapTo(md, QPoint()), md.editor.size())
    preview = QRect(md.preview.mapTo(md, QPoint()), md.preview.size())
    assert md.rect().contains(editor), (md.rect(), editor)
    assert md.rect().contains(preview), (md.rect(), preview)
    assert not editor.intersects(preview), (editor, preview)
    assert editor.width() >= 200 and preview.width() >= 200, (win.size(), nc.size(), md.size(), editor, preview)
    for button in nc.findChildren(QPushButton):
        assert nc.rect().contains(QRect(button.mapTo(nc, QPoint()), button.size()))
    direction = "horizontal" if md.splitter.orientation() == Qt.Horizontal else "vertical"
    file = output / f"notes-{width}.png"
    assert win.grab().save(str(file))
    measurements.append({"window_width": win.width(), "mode": direction,
                         "editor": editor.getRect(), "preview": preview.getRect(),
                         "screenshot": str(file)})
print(json.dumps(measurements, indent=2))
app.removeEventFilter(win.hotkeys)
win.close()
print("MARKDOWN GEOMETRY PASSED")
