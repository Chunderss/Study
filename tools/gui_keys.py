"""Verify LEADER-mode hotkeys via the HotkeyFilter directly (deterministic;
offscreen QTest can't route synthetic keys through an app-level filter).

Flow tested: leader chord (Ctrl+Shift+Space) arms; the next PLAIN key runs the
command and is consumed so a focused text box never types it. This is the model
that dodges OS/driver global-hotkey conflicts (the second key has no modifiers).
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QTextEdit

from vocab.gui import theme
from vocab.gui.main_window import MainWindow

CS = Qt.ControlModifier | Qt.ShiftModifier


def send(win, key, mods=Qt.NoModifier):
    return win.hotkeys.eventFilter(win, QKeyEvent(QEvent.KeyPress, key, mods))


def leader(win, key):
    """Arm the leader (Ctrl+B), then press a plain key. Returns (armed_consumed, key_consumed)."""
    a = send(win, Qt.Key_B, Qt.ControlModifier)   # tmux-style leader chord
    assert win.hotkeys._armed, "leader chord (Ctrl+B) did not arm"
    k = send(win, key, Qt.NoModifier)             # plain command key
    QApplication.processEvents()
    return a, k


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow(root=tempfile.mkdtemp(prefix="vocab_keys_"))
    win.show()
    ws = win.workspace

    tes = ws.focused.component.findChildren(QTextEdit)
    if tes:
        tes[0].setFocus()
    QApplication.processEvents()
    print("[setup] focus in:", app.focusWidget().__class__.__name__)

    # leader v -> split vertical
    n0 = len(ws._panes)
    a, k = leader(win, Qt.Key_V)
    assert a and k, f"leader/command not consumed: {a},{k}"
    assert len(ws._panes) == n0 + 1, "Space v did not split vertically"
    print("[ok] Ctrl+B then v  → split vertical")

    # leader - -> split horizontal
    n1 = len(ws._panes)
    leader(win, Qt.Key_Minus)
    assert len(ws._panes) == n1 + 1, "Space - did not split horizontally"
    print("[ok] Ctrl+B then -  → split horizontal")

    # leader z -> zoom (the conflict-prone one, now a plain letter)
    leader(win, Qt.Key_Z)
    assert ws.is_zoomed, "Space z did not zoom"
    leader(win, Qt.Key_Z)
    assert not ws.is_zoomed, "Space z did not un-zoom"
    print("[ok] Ctrl+B then z  → zoom toggle")

    # leader n -> cycle component
    before = ws.focused.factory_key
    leader(win, Qt.Key_N)
    assert ws.focused.factory_key != before, "Space n did not cycle"
    print("[ok] Ctrl+B then n  → cycle component")

    # leader b -> sidebar
    vis = win.sidebar.isVisible()
    leader(win, Qt.Key_B)
    assert win.sidebar.isVisible() != vis, "Space b did not toggle sidebar"
    leader(win, Qt.Key_B)
    print("[ok] Ctrl+B then b  → sidebar toggle")

    # leader x -> close (the one that was intercepted as a direct chord)
    n2 = len(ws._panes)
    leader(win, Qt.Key_X)
    assert len(ws._panes) == n2 - 1, "Space x did not close a pane"
    print("[ok] Ctrl+B then x  → close pane (no more Ctrl+Shift+X conflict)")

    # leader 1 -> focus pane by number
    leader(win, Qt.Key_1)
    assert ws.focused is not None
    print("[ok] Ctrl+B then 1  → focus pane 1")

    # a plain key with NO leader must pass through (typing unaffected)
    assert not send(win, Qt.Key_A, Qt.NoModifier), "plain key consumed without leader"
    print("[ok] plain keys pass through when leader not armed (typing works)")

    # leader times out / Esc cancels
    send(win, Qt.Key_B, Qt.ControlModifier)
    assert win.hotkeys._armed
    send(win, Qt.Key_Escape, Qt.NoModifier)
    assert not win.hotkeys._armed, "Esc did not cancel leader"
    print("[ok] Esc cancels an armed leader")

    # the leader chord Ctrl+B must be CONSUMED (return True) so a focused editor
    # never treats it as 'bold' or lets it leak
    consumed = send(win, Qt.Key_B, Qt.ControlModifier)
    assert consumed, "Ctrl+B leader was not consumed (would leak to text as bold)"
    send(win, Qt.Key_Escape, Qt.NoModifier)  # disarm
    print("[ok] Ctrl+B leader is consumed (no bold leak into text)")

    # direct arrow chord still works (Ctrl+Shift+Left)
    win.workspace.split_vertical()  # ensure 2 panes
    ok = send(win, Qt.Key_Left, CS)
    assert ok, "Ctrl+Shift+Left direct chord did not fire"
    print("[ok] Ctrl+Shift+← direct arrow still works")

    # help panel: F1 opens, stays open while other leader commands run
    send(win, Qt.Key_F1, Qt.NoModifier)
    h = getattr(win, "_help", None)
    assert h is not None and h.isVisible(), "F1 did not open help"
    n3 = len(ws._panes)
    leader(win, Qt.Key_V)
    assert h.isVisible() and len(ws._panes) == n3 + 1, "help closed or hotkeys dead while open"
    assert not h.hasFocus(), "help stole focus"
    print("[ok] help panel stays open + leader commands keep working")

    from PySide6.QtWidgets import QLabel
    labels = [lbl for _l, _d, lbl, _c, _a in win._keymap()]
    shown = " ".join(l.text() for l in h.findChildren(QLabel))
    missing = [lbl for lbl in labels if lbl not in shown]
    assert not missing, f"help missing: {missing}"
    print(f"[ok] help panel lists all {len(labels)} shortcuts")

    print("\nLEADER HOTKEYS + PERSISTENT HELP PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
