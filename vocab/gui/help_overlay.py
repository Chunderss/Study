"""Keyboard-shortcuts panel.

A non-modal reference panel that can stay OPEN while you keep using shortcuts.
Key design points that make "keep it up while doing other keybinds" work:

  * It never grabs keyboard focus (WA_ShowWithoutActivating + focus policy
    NoFocus on every child), so the app-level HotkeyFilter keeps receiving keys
    while the panel is visible.
  * It doesn't cover/interceptt input for the whole window — it's a slim panel
    docked on the right, not a full-screen dim overlay, so panes stay usable.
  * Toggle it with the same help key; it does not auto-dismiss on other keys.

Rendered entirely from MainWindow._keymap() so help can't drift from bindings.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel, QScrollArea,
                               QVBoxLayout, QWidget)

from . import theme

PANEL_WIDTH = 300


class HelpOverlay(QFrame):
    def __init__(self, keymap, parent=None):
        super().__init__(parent)
        self.setObjectName("HelpPanel")
        # do NOT take focus — so global hotkeys keep firing while this is open
        self.setFocusPolicy(Qt.NoFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            f"#HelpPanel {{ background: {theme.BG_PANEL}; border-left: 2px solid {theme.WALNUT}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        title = QLabel("Keyboard shortcuts")
        title.setObjectName("Title")
        title.setFocusPolicy(Qt.NoFocus)
        root.addWidget(title)
        sub = QLabel("Press Ctrl+B, then the key (tmux-style). Stays open; toggle with F1.",
                     objectName="Muted")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # scrollable body (in case the list grows)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.NoFocus)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setFocusPolicy(Qt.NoFocus)
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(8)

        cats: dict[str, list] = {}
        for leader, direct, label, cat, _action in keymap:
            parts = []
            if leader:
                parts.append(f"Ctrl+B  {leader}")
            if direct:
                parts.append(direct[0][2])
            shown = "  /  ".join(parts) if parts else "?"
            cats.setdefault(cat, []).append((shown, label))

        for cat, rows in cats.items():
            head = QLabel(cat.upper())
            head.setFocusPolicy(Qt.NoFocus)
            head.setStyleSheet(f"color: {theme.WALNUT_HI}; font-weight: bold; margin-top: 4px;")
            bl.addWidget(head)
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(3)
            grid.setColumnStretch(1, 1)
            for r, (key, label) in enumerate(rows):
                kb = QLabel(key)
                kb.setFocusPolicy(Qt.NoFocus)
                kb.setStyleSheet(
                    f"background: {theme.BG_INPUT}; color: {theme.INK};"
                    f" border: 1px solid {theme.RULE}; border-radius: 5px;"
                    f" padding: 1px 6px; font-weight: bold;")
                desc = QLabel(label)
                desc.setFocusPolicy(Qt.NoFocus)
                desc.setWordWrap(True)
                grid.addWidget(kb, r, 0, alignment=Qt.AlignLeft | Qt.AlignTop)
                grid.addWidget(desc, r, 1)
            bl.addLayout(grid)
        bl.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

    def place(self, parent_rect) -> None:
        """Dock on the right edge of the parent, full height."""
        self.setGeometry(parent_rect.width() - PANEL_WIDTH, 0,
                         PANEL_WIDTH, parent_rect.height())
