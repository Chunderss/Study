"""GUI entry point:  python -m vocab.gui   (or the `vocab-gui` script)."""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    ap = argparse.ArgumentParser(prog="vocab-gui", description="Vocab Study desktop app.")
    ap.add_argument("--home", help="Override data directory (default: platform data dir)")
    args = ap.parse_args()

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.stderr.write(
            "The desktop app needs PySide6. Install it with:\n"
            '    pip install "vocab-study[gui]"\n'
            "or run the terminal version:  python -m vocab\n")
        sys.exit(1)

    from .main_window import MainWindow
    from . import theme

    app = QApplication(sys.argv)
    app.setApplicationName("Vocab Study")
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow(root=args.home)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
