"""Deliver shortcuts through QWindow, as platform keyboard events arrive.

Sending QTest keys directly to a QWidget bypasses the window event delivery
that previously cancelled the leader on key release.
"""
import pytest

from .test_gui_reliability import qt, window, select
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QLineEdit, QMessageBox, QVBoxLayout, QWidget


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


def test_held_component_command_cannot_type_into_replacement_editor(window, qt):
    native = focus(window, window.workspace.focused.component.add_input, qt)
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    QTest.keyPress(native, Qt.Key_N)
    assert window.workspace.focused.factory_key == "notes"
    notes = window.workspace.focused.component
    notes.editor.setFocus()
    qt.processEvents()
    for _ in range(3):
        qt.sendEvent(notes.editor, QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.NoModifier, "n", True))
    QTest.keyRelease(native, Qt.Key_N)
    assert notes.editor.toPlainText() == "original"


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


def test_context_menu_escape_cancels_leader_without_eating_next_key(window, qt):
    edit = window.workspace.focused.component.add_input
    native = focus(window, edit, qt)
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    assert window.hotkeys._armed
    menu = edit.createStandardContextMenu()
    try:
        menu.popup(edit.mapToGlobal(edit.rect().center()))
        qt.processEvents()
        assert menu.isVisible()
        QTest.keyClick(menu, Qt.Key_Escape)
        qt.processEvents()
        assert not menu.isVisible()
        assert not window.hotkeys._armed
        QTest.keyClick(native, Qt.Key_V)
        assert edit.text() == "v"
        assert len(window.workspace._panes) == 1
    finally:
        menu.close()
        menu.deleteLater()


def test_focus_proxy_widget_receives_native_shortcuts(window, qt):
    container = QWidget(window)
    edit = QLineEdit(container)
    container.setFocusProxy(edit)
    container.show()
    try:
        native = focus(window, container, qt)
        assert qt.focusWidget() is edit
        leader(native, Qt.Key_V)
        assert len(window.workspace._panes) == 2
        assert edit.text() == ""
    finally:
        container.deleteLater()
        qt.processEvents()


def test_window_deactivation_without_new_focus_cancels_leader(window, qt):
    edit = window.workspace.focused.component.add_input
    native = focus(window, edit, qt)
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    assert window.hotkeys._armed
    other = QWidget()
    other.setFocusPolicy(Qt.NoFocus)
    other.show()
    try:
        other.activateWindow()
        qt.processEvents()
        assert qt.activeWindow() is other
        assert qt.focusWidget() is None
        assert not window.hotkeys._armed
    finally:
        other.close()
        other.deleteLater()
        qt.processEvents()
    native = focus(window, edit, qt)
    QTest.keyClick(native, Qt.Key_V)
    assert edit.text() == "v"
    assert len(window.workspace._panes) == 1


def test_temporary_missing_focus_does_not_forget_held_command(window, qt):
    native = focus(window, window.workspace.focused.component.add_input, qt)
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    QTest.keyPress(native, Qt.Key_N)
    notes = window.workspace.focused.component
    current = qt.focusWidget()
    current.clearFocus()
    assert qt.focusWidget() is None
    notes.editor.setFocus()
    qt.processEvents()
    qt.sendEvent(notes.editor, QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.NoModifier, "n", True))
    QTest.keyRelease(native, Qt.Key_N)
    assert notes.editor.toPlainText() == "original"


def test_application_deactivation_cancels_pending_leader(window, qt):
    edit = window.workspace.focused.component.add_input
    native = focus(window, edit, qt)
    QTest.keyClick(native, Qt.Key_B, Qt.ControlModifier)
    assert window.hotkeys._armed
    qt.applicationStateChanged.emit(Qt.ApplicationInactive)
    assert not window.hotkeys._armed
    qt.applicationStateChanged.emit(Qt.ApplicationActive)
    QTest.keyClick(native, Qt.Key_V)
    assert edit.text() == "v"
    assert len(window.workspace._panes) == 1


def test_cancelled_close_preserves_focused_note_shortcuts(window, qt, monkeypatch):
    window.workspace.set_focused_component("notes")
    notes = window.workspace.focused.component
    select(notes, "One")
    notes.editor.setPlainText("unsaved draft")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    assert not window.close()
    native = focus(window, notes.editor, qt)
    leader(native, Qt.Key_R)
    assert notes.markdown._preview_panel.isHidden()
    assert notes.editor.toPlainText() == "unsaved draft"


def test_stopped_hotkeys_do_not_follow_focus_until_restarted(window, qt):
    window.hotkeys.stop()
    window.workspace.split_vertical()
    edit = window.workspace.focused.component.add_input
    native = focus(window, edit, qt)
    leader(native, Qt.Key_V)
    assert edit.text() == "v"
    assert len(window.workspace._panes) == 2

    window.hotkeys.start()
    leader(native, Qt.Key_V)
    assert len(window.workspace._panes) == 3
