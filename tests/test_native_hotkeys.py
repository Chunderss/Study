"""Deliver shortcuts through QWindow, as platform keyboard events arrive.

Sending QTest keys directly to a QWidget bypasses the window event delivery
that previously cancelled the leader on key release.
"""
import pytest

from .test_gui_reliability import qt, window, select
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QVBoxLayout


def focus(window, widget, qt):
    window.activateWindow()
    widget.setFocus()
    qt.processEvents()
    return window.windowHandle()


def leader(native, key, modifiers=Qt.NoModifier):
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    QTest.keyClick(native, key, modifiers)


@pytest.mark.parametrize("held_control", [False, True])
def test_native_leader_preview_toggle_and_typing(window, qt, held_control):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "One")
    native = focus(window, notes.editor, qt)
    mods = Qt.ControlModifier if held_control else Qt.NoModifier
    for visible in (False, True):
        QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
        assert window.hotkeys._armed, "Native key releases must not cancel Ctrl+B"
        QTest.keyClick(native, Qt.Key_R, mods)
        assert not window.hotkeys._armed
        assert notes.markdown.preview.isVisible() is visible
        assert notes.editor.toPlainText() == "original"
    QTest.keyClick(native, Qt.Key_A)
    assert notes.editor.toPlainText().count("a") == 2  # original + one typed a


def test_native_pane_commands_execute_once(window, qt):
    native = focus(window, window.workspace.focused.component.add_input, qt)
    leader(native, Qt.Key_V)
    assert len(window.workspace._panes) == 2
    leader(native, Qt.Key_Minus)
    assert len(window.workspace._panes) == 3
    leader(native, Qt.Key_Z)
    assert window.workspace.is_zoomed
    leader(native, Qt.Key_Z)
    assert not window.workspace.is_zoomed
    leader(native, Qt.Key_N)
    assert window.workspace.focused.factory_key == "notes"
    leader(native, Qt.Key_X)
    assert len(window.workspace._panes) == 2


def test_native_leader_saves_focused_note(window, qt):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "One")
    notes.editor.setPlainText("save this draft")
    native = focus(window, notes.editor, qt)
    leader(native, Qt.Key_S)
    assert window.app.storage.load_note("Book", "One") == "save this draft"


def test_native_escape_cancels_leader_without_swallowing_typing(window, qt):
    edit = window.workspace.focused.component.add_input
    native = focus(window, edit, qt)
    leader(native, Qt.Key_Escape)
    assert not window.hotkeys._armed
    QTest.keyClick(native, Qt.Key_V)
    assert edit.text() == "v"
    assert len(window.workspace._panes) == 1


@pytest.mark.parametrize("modal", [False, True])
def test_native_dialog_keys_cannot_trigger_workspace_shortcuts(window, qt, modal):
    native = focus(window, window.workspace.focused.component.add_input, qt)
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    assert window.hotkeys._armed
    dialog = QDialog(window)
    dialog.setModal(modal)
    layout = QVBoxLayout(dialog)
    edit = QLineEdit()
    layout.addWidget(edit)
    dialog.show()
    try:
        dialog_native = focus(dialog, edit, qt)
        leader(dialog_native, Qt.Key_V)
        assert edit.text() == "v"
        assert not window.hotkeys._armed
        assert len(window.workspace._panes) == 1
    finally:
        dialog.close()
        dialog.deleteLater()
        qt.processEvents()
    native = focus(window, window.workspace.focused.component.add_input, qt)
    leader(native, Qt.Key_V)
    assert len(window.workspace._panes) == 2
