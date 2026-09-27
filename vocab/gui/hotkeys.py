"""Window-scoped hotkey filter with a tmux-style LEADER mode.

Why leader mode? Direct chords (Ctrl+Shift+X, +J, ...) collide with OS/driver
global hotkeys that grab them before our app sees them, and the set of
offenders differs per machine. A leader sequence dodges this: press ONE leader
chord, then a PLAIN letter (no modifiers). Nothing else in the OS grabs a bare
letter, and the filter on the focused widget consumes that letter before its
text box can type it. Do not install Python event filters on QApplication:
they caused native QtWebEngine crashes in our PySide6 6.11 tests.
Following keyboard focus covers Chromium's
input widgets without filtering every application event.

Two binding kinds:
  * direct chords  : (modifiers, key) -> action     (kept for arrows etc.)
  * leader keys    : plain key -> action, active only right after the leader

Leader flow: leader chord pressed -> `armed=True` (with a short timeout). The
next KeyPress is looked up in the leader table and consumed; then disarm.
Esc while armed cancels. Modifiers on the second press are ignored (so Shift or
CapsLock don't matter).

Shifted-key aliasing (physical '\' -> Key_Bar under Shift, etc.) still applies to
direct chords; leader keys are plain so we alias letters case-insensitively via
the text of the event instead.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

_SHIFT_ALIASES = {
    Qt.Key_Backslash: Qt.Key_Bar, Qt.Key_Minus: Qt.Key_Underscore,
    Qt.Key_Period: Qt.Key_Greater, Qt.Key_Comma: Qt.Key_Less,
    Qt.Key_Slash: Qt.Key_Question, Qt.Key_Semicolon: Qt.Key_Colon,
    Qt.Key_Equal: Qt.Key_Plus,
    Qt.Key_1: Qt.Key_Exclam, Qt.Key_2: Qt.Key_At, Qt.Key_3: Qt.Key_NumberSign,
    Qt.Key_4: Qt.Key_Dollar, Qt.Key_5: Qt.Key_Percent, Qt.Key_6: Qt.Key_AsciiCircum,
    Qt.Key_7: Qt.Key_Ampersand, Qt.Key_8: Qt.Key_Asterisk, Qt.Key_9: Qt.Key_ParenLeft,
    Qt.Key_0: Qt.Key_ParenRight,
}

_MOD_FLAGS = (Qt.ControlModifier, Qt.ShiftModifier, Qt.AltModifier, Qt.MetaModifier)


def _norm_mods(mods) -> int:
    m = 0
    for flag in _MOD_FLAGS:
        if mods & flag:
            m |= int(flag.value)
    return m


class HotkeyFilter(QObject):
    LEADER_TIMEOUT_MS = 2000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._direct: Dict[Tuple[int, int], Callable[[], None]] = {}
        self._leader: Dict[int, Callable[[], None]] = {}   # plain key -> action
        self._leader_chord: Optional[Tuple[int, int]] = None
        self._armed = False
        self._consumed_keys = set()
        self._on_arm_change: Optional[Callable[[bool], None]] = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._disarm)
        self._app = None
        self._focused_widgets = []

    # ---- scoped installation -------------------------------------------
    def start(self) -> None:
        """Follow keyboard focus within the owning window until stop()."""
        if self._app is not None:
            return
        self._app = QApplication.instance()
        owner = self.parent()
        owner.installEventFilter(self)
        self._app.focusChanged.connect(self._focus_changed)
        self._app.applicationStateChanged.connect(self._application_state_changed)
        self._focus_changed(None, self._app.focusWidget())

    def stop(self) -> None:
        if self._app is None:
            return
        self._app.focusChanged.disconnect(self._focus_changed)
        self._app.applicationStateChanged.disconnect(self._application_state_changed)
        self._app = None
        self._clear_focus_filters()
        owner = self.parent()
        if owner is not None and isValid(owner):
            owner.removeEventFilter(self)
        self._disarm()
        self._consumed_keys.clear()

    def _clear_focus_filters(self) -> None:
        for widget in self._focused_widgets:
            if isValid(widget):
                widget.removeEventFilter(self)
        self._focused_widgets.clear()

    def _focus_changed(self, _old, new) -> None:
        self._clear_focus_filters()
        if new is None:
            # Replacing/reparenting a pane briefly clears focus while a command
            # key is still held. Keep suppressing its repeats until key release;
            # leaving the application/window is handled separately.
            return
        owner = self.parent()
        if (not isinstance(new, QWidget) or not isValid(new) or
                not isinstance(owner, QWidget) or not isValid(owner) or
                new.window() is not owner.window() or
                QApplication.activeModalWidget() is not None):
            self._disarm()
            self._consumed_keys.clear()
            return
        # Qt reports the actual focus receiver, including a control's focus
        # proxy, so there is no need to traverse the control's children.
        if new is not owner:
            new.installEventFilter(self)
            self._focused_widgets.append(new)

    def _application_state_changed(self, state) -> None:
        if state != Qt.ApplicationActive:
            self._disarm()
            self._consumed_keys.clear()

    # ---- registration ---------------------------------------------------
    def bind_direct(self, modifiers, key, action) -> None:
        mods = _norm_mods(modifiers)
        self._direct[(mods, int(key))] = action
        alias = _SHIFT_ALIASES.get(key)
        if alias is not None:
            self._direct[(mods, int(alias))] = action

    def bind_leader_key(self, key, action) -> None:
        self._leader[int(key)] = action

    def set_leader(self, modifiers, key) -> None:
        self._leader_chord = (_norm_mods(modifiers), int(key))

    def set_arm_callback(self, fn) -> None:
        """fn(bool) is called when leader mode arms/disarms (for a status hint)."""
        self._on_arm_change = fn

    # ---- state ----------------------------------------------------------
    def _arm(self) -> None:
        self._armed = True
        self._timer.start(self.LEADER_TIMEOUT_MS)
        if self._on_arm_change:
            self._on_arm_change(True)

    def _disarm(self) -> None:
        if self._armed:
            self._armed = False
            self._timer.stop()
            if self._on_arm_change:
                self._on_arm_change(False)

    # ---- event handling -------------------------------------------------
    def eventFilter(self, obj, ev):
        if (ev.type() in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate) or
                (ev.type() == QEvent.FocusOut and ev.reason() == Qt.PopupFocusReason)):
            # Popup menus can grab the keyboard without changing focusWidget().
            # Their Escape must not leave a workspace command armed underneath.
            self._disarm()
            self._consumed_keys.clear()
            return False
        if ev.type() not in (QEvent.KeyPress, QEvent.KeyRelease, QEvent.ShortcutOverride):
            return False
        # Native input passes through QWindow before reaching the focused
        # QWidget. Ignore that intermediate delivery without changing leader
        # state; otherwise releasing Ctrl+B cancels the sequence. Handle the
        # widget delivery only, so each command runs once.
        if not isinstance(obj, QWidget):
            return False
        owner = self.parent()
        # A previously focused widget can move into a dialog before the focus
        # notification arrives. Workspace commands must stay in their window.
        if isinstance(owner, QWidget) and (
                obj.window() is not owner.window()
                or QApplication.activeModalWidget() is not None):
            self._disarm()
            self._consumed_keys.clear()
            return False
        key = int(ev.key())
        mods = _norm_mods(ev.modifiers())
        chord = (mods, key)
        if ev.type() == QEvent.KeyRelease:
            if not ev.isAutoRepeat():
                self._consumed_keys.discard(key)
            return False
        bound = self._armed or chord == self._leader_chord or chord in self._direct
        if ev.type() == QEvent.ShortcutOverride:
            if bound or key in self._consumed_keys:
                ev.accept()
                return True
            return False
        if ev.isAutoRepeat() and (bound or key in self._consumed_keys):
            return True

        # 1) if armed, the next key is a leader command (ignore modifiers on it)
        if self._armed:
            # a bare modifier keypress (Ctrl/Shift alone) shouldn't count
            if key in (int(Qt.Key_Control), int(Qt.Key_Shift), int(Qt.Key_Alt),
                       int(Qt.Key_Meta)):
                return False
            self._disarm()
            self._consumed_keys.add(key)
            if key == int(Qt.Key_Escape):
                return True  # cancel
            action = self._leader.get(key)
            if action is None:
                # also try case-folded letter via text (Shift+letter etc.)
                t = ev.text().lower()
                if t:
                    upper = t.upper()
                    action = self._leader.get(ord(upper)) if len(upper) == 1 else None
            if action is not None:
                action()
            return True  # consume regardless, so stray keys don't leak

        # 2) leader chord itself?
        if self._leader_chord is not None and (mods, key) == self._leader_chord:
            self._consumed_keys.add(key)
            self._arm()
            return True

        # 3) direct chord?
        action = self._direct.get((mods, key))
        if action is not None:
            self._consumed_keys.add(key)
            action()
            return True
        return False
