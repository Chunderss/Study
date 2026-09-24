"""Render PNG screenshots of the tiling workspace offscreen."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from vocab.gui import theme
from vocab.gui.main_window import MainWindow
from vocab.gui.components import NotesComponent

app = QApplication(sys.argv)
app.setStyleSheet(theme.stylesheet())
win = MainWindow(root=tempfile.mkdtemp(prefix="vocab_shot_"))
win.resize(1040, 700)
win.show()

# seed a module with vocab + a note
win.app.cmd_create("The Great Gatsby")
win.app.cmd_use("The Great Gatsby")
for w, d in [("ephemeral", "lasting a very short time"),
             ("verdant", "green with vegetation"),
             ("supercilious", "behaving as though superior to others")]:
    win.app.cmd_add(w, manual_def=d)
win.app.storage.save_note("The Great Gatsby", "Chapter 1",
    "# Chapter 1 — notes\n\n- Nick Carraway narrates\n- **Gatsby** throws lavish parties\n- Theme: the *ephemeral* American Dream")
win._refresh_modules(); win.ctx.module_changed.emit("The Great Gatsby")
app.processEvents()

ws = win.workspace
# split vertical, put Notes in the right pane, select the note
ws.split_vertical()
ws.set_focused_component("notes")
nc = ws.focused.component
items = nc.note_list.findItems("Chapter 1", Qt.MatchExactly)
if items:
    nc.note_list.setCurrentItem(items[0])
app.processEvents()
out1 = os.path.join(os.getcwd(), "docs_split.png")
win.grab().save(out1)
print("SPLIT_SHOT", out1)

# now open a study pane (splits again)
win._open_study(["The Great Gatsby"])
sv = ws.focused.component
sv._reveal()
app.processEvents()
out2 = os.path.join(os.getcwd(), "docs_study_split.png")
win.grab().save(out2)
print("STUDY_SPLIT_SHOT", out2)
