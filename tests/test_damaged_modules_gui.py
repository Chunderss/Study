"""Damaged module data must not break focus handling, the sidebar or panes."""
import sys

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from .test_gui_reliability import qt, window
from vocab.gui.components import VocabComponent


@pytest.fixture
def slot_errors(monkeypatch):
    # Qt reports slot exceptions through sys.excepthook instead of raising.
    errors = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, error, tb: errors.append(error))
    return errors


def move_focus(window, qt):
    component = window.workspace.focused.component
    for widget in (window.module_list, component, window.module_list):
        widget.setFocus()
        qt.processEvents()


def test_damaged_current_module_keeps_focus_handling_quiet(window, qt, slot_errors):
    stats = window.app.paths.stats_file("Book")
    stats.write_text("{corrupt", encoding="utf-8")
    logged = []
    window.ctx.log.connect(logged.append)
    for _ in range(3):
        move_focus(window, qt)
        window.ctx.changed.emit()
        qt.processEvents()
    assert slot_errors == []
    assert "due: ?" in window.status.text()
    assert "unreadable module data" in window.status.text()
    assert str(stats) in window.status.toolTip()
    # One diagnostic per distinct problem, not one per focus change.
    assert len([m for m in logged if str(stats) in m]) == 1
    assert stats.read_text(encoding="utf-8") == "{corrupt"


def test_one_damaged_module_leaves_the_sidebar_usable(window, qt, slot_errors):
    window.app.cmd_create("Other")
    words = window.app.paths.words_file("Book")
    words.write_text("[", encoding="utf-8")
    renamed = window.app.paths.modules_dir / "Old [copy]"
    window.app.cmd_create("Spare")
    window.app.paths.module_dir("Spare").rename(renamed)
    window.ctx.changed.emit()
    qt.processEvents()
    rows = [window.module_list.item(i) for i in range(window.module_list.count())]
    labels = [row.text() for row in rows]
    assert labels[0] == "Book  (unreadable)" and str(words) in rows[0].toolTip()
    assert labels[1].startswith("Other  (0w")
    assert labels[2] == "Old [copy]  (unsupported folder name)"
    assert rows[2].flags() == Qt.NoItemFlags
    assert not window.module_list.signalsBlocked()
    window.module_list.setCurrentRow(1)
    assert window.app.current_module == "Other"
    vocab = window.workspace.focused.component
    assert isinstance(vocab, VocabComponent)
    window.module_list.setCurrentRow(0)
    assert window.app.current_module == "Book"
    assert "Could not read this module's vocabulary" in vocab.words.toPlainText()
    assert slot_errors == []


def test_unknown_dictionary_setting_does_not_break_the_status_bar(window, qt, slot_errors):
    window.app.config.active_dictionary = "missing-dictionary"
    move_focus(window, qt)
    assert slot_errors == []
    assert "dict: ?" in window.status.text()
    assert "missing-dictionary" in window.status.toolTip()
