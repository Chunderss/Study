"""Dark theme: a warm, focused console look (loosely 'walnut & linen' on dark).

One place for the palette + Qt stylesheet so the whole app stays consistent and
the look is easy to tweak later.
"""
from __future__ import annotations

# Palette ---------------------------------------------------------------------
INK        = "#e8e0d4"   # primary text (warm off-white)
INK_DIM    = "#9c948a"   # secondary text
BG         = "#1b1917"   # window background (near-black, warm)
BG_PANEL   = "#242019"   # panels / cards
BG_INPUT   = "#15130f"   # input fields / console
WALNUT     = "#8c6d4f"   # accent (brand walnut)
WALNUT_HI  = "#a9865f"   # accent hover
LINEN      = "#f3ede6"   # bright text on accent
RULE       = "#3a352d"   # borders / separators
GOOD       = "#7ea86b"   # correct
BAD        = "#c07a5b"   # incorrect
MONO       = "Cascadia Mono, Consolas, 'Courier New', monospace"


def stylesheet() -> str:
    return f"""
    QWidget {{
        background: {BG};
        color: {INK};
        font-family: {MONO};
        font-size: 14px;
    }}
    QLabel#Title {{ color: {WALNUT_HI}; font-size: 20px; font-weight: bold; }}
    QLabel#Muted {{ color: {INK_DIM}; }}

    /* Sidebar list of vocab lists */
    QListWidget {{
        background: {BG_PANEL};
        border: 1px solid {RULE};
        border-radius: 8px;
        padding: 4px;
        outline: none;
    }}
    QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
    QListWidget::item:selected {{ background: {WALNUT}; color: {LINEN}; }}
    QListWidget::item:hover {{ background: {RULE}; }}

    /* Console / big text areas */
    QTextEdit, QPlainTextEdit {{
        background: {BG_INPUT};
        border: 1px solid {RULE};
        border-radius: 8px;
        padding: 8px;
        selection-background-color: {WALNUT};
        selection-color: {LINEN};
    }}

    /* Command line + text inputs */
    QLineEdit {{
        background: {BG_INPUT};
        border: 1px solid {RULE};
        border-radius: 8px;
        padding: 8px 10px;
        color: {INK};
    }}
    QLineEdit:focus {{ border: 1px solid {WALNUT}; }}

    QPushButton {{
        background: {BG_PANEL};
        border: 1px solid {RULE};
        border-radius: 8px;
        padding: 8px 16px;
        color: {INK};
    }}
    QPushButton:hover {{ border: 1px solid {WALNUT}; }}
    QPushButton:default {{ background: {WALNUT}; color: {LINEN}; border: 1px solid {WALNUT}; }}
    QPushButton#Good:hover {{ border: 1px solid {GOOD}; }}
    QPushButton#Bad:hover  {{ border: 1px solid {BAD}; }}
    QPushButton:disabled {{ color: {INK_DIM}; }}

    /* Progress bar in study view */
    QProgressBar {{
        background: {BG_INPUT};
        border: 1px solid {RULE};
        border-radius: 6px;
        text-align: center;
        color: {INK_DIM};
        height: 10px;
    }}
    QProgressBar::chunk {{ background: {WALNUT}; border-radius: 6px; }}

    /* Status bar */
    QStatusBar {{ background: {BG_PANEL}; color: {INK_DIM}; border-top: 1px solid {RULE}; }}
    QStatusBar QLabel {{ color: {INK_DIM}; }}

    QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {RULE}; border-radius: 5px; min-height: 24px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
    """
