"""Screenshot the help overlay offscreen."""
import os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from vocab.gui import theme
from vocab.gui.main_window import MainWindow

app = QApplication(sys.argv)
app.setStyleSheet(theme.stylesheet())
win = MainWindow(root=tempfile.mkdtemp(prefix="vocab_help_"))
win.resize(1040, 700)
win.show()
win.app.cmd_create("The Great Gatsby"); win.app.cmd_use("The Great Gatsby")
win._refresh_modules()
app.processEvents()
win._toggle_help()
app.processEvents()
out = os.path.join(os.getcwd(), "docs_help.png")
win.grab().save(out)
print("HELP_SHOT", out)
