"""Desktop entry point, also used by the windowed executable."""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="VocabStudy", description="Vocab Study desktop app.")
    ap.add_argument("--home", help="Override data directory (default: platform data dir)")
    ap.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        from PySide6.QtCore import QTimer
    except ImportError as error:
        if sys.stderr:
            sys.stderr.write(f"Could not load Qt: {error}\n"
                             'Source install: pip install "vocab-study[gui]"\n')
        raise SystemExit(1)

    app = QApplication([sys.argv[0]])
    app.setApplicationName("Vocab Study")
    from ..core.paths import get_paths
    paths = get_paths(args.home)
    logfile = paths.root / "desktop.log"
    logging.basicConfig(filename=logfile, level=logging.ERROR,
                        format="%(asctime)s %(levelname)s %(message)s")

    def report_error(kind, error, traceback):
        logging.error("Desktop error", exc_info=(kind, error, traceback))
        if args.smoke_test:
            app.exit(1)
        else:
            QMessageBox.critical(None, "Vocab Study error",
                                 f"{error}\n\nDetails were saved to {logfile}")

    sys.excepthook = report_error
    try:
        # PyInstaller places bundled data inside _MEIPASS, never the working dir.
        if getattr(sys, "frozen", False):
            import nltk
            nltk.data.path.insert(0, str(Path(sys._MEIPASS) / "nltk_data"))
        from .main_window import MainWindow
        from . import theme
        app.setStyleSheet(theme.stylesheet())
        win = MainWindow(root=args.home)
        win.show()
    except Exception:
        report_error(*sys.exc_info())
        raise SystemExit(1)

    if args.smoke_test:
        def smoke():
            try:
                from PySide6 import QtPdf, QtPdfWidgets, QtWebEngineWidgets
                from ..dictionaries import get_dictionary
                assert get_dictionary("wordnet").lookup("test")
                from .markdown_editor import MarkdownEditor
                editor = MarkdownEditor()
                editor.editor.setPlainText("# Desktop ready\n\n**Offline**")
                editor.render()
                assert "Desktop ready" in editor.preview.toPlainText()
                editor.deleteLater()
                # Exercise the platform's window-to-widget key delivery in the
                # packaged app as well as the source regression tests.
                from PySide6.QtCore import Qt
                from PySide6.QtTest import QTest
                win.activateWindow()
                win.workspace.focused.component.focus_default()
                app.processEvents()
                native = win.windowHandle()
                QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
                assert win.hotkeys._armed, "Ctrl+B did not arm the leader"
                QTest.keyClick(native, Qt.Key_V)
                assert len(win.workspace._panes) == 2, "Leader split did not run once"
                # Verify that the new learning UI and recovery storage are
                # available in the frozen build, with no model or network.
                win.app.cmd_create("DesktopCheck")
                win.app.cmd_use("DesktopCheck")
                win.app.storage.save_note("DesktopCheck", "Recovery", "saved")
                buffer = win.ctx.notes.open("DesktopCheck", "Recovery")
                buffer.document.setPlainText("draft")
                buffer.flush_recovery()
                assert win.app.storage.load_note_draft("DesktopCheck", "Recovery")["content"] == "draft"
                buffer.save()
                from ..core.learning import LearningStore
                from .learning import LearningComponent, ReviewDialog
                store = LearningStore(win.app.storage)
                item = store.save_capture("DesktopCheck", kind="concept", prompt="Explain the idea",
                                          quote="A source passage.")
                review = ReviewDialog(win.ctx, "DesktopCheck", item)
                assert review.reference.isHidden()
                review.answer.setPlainText("My explanation.")
                review.show_source()
                review.rate("good")
                assert store.load("DesktopCheck")[item["id"]]["card"]["reps"] == 1
                review.deleteLater()
                win.workspace.set_focused_component("learning")
                assert isinstance(win.workspace.focused.component, LearningComponent)
            except Exception:
                report_error(*sys.exc_info())
                app.exit(1)
            else:
                win.close()
                app.exit(0)
        QTimer.singleShot(100, smoke)
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
