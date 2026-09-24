"""Collapsible module sidebar plus a keyboard-first tiling workspace.

Ctrl+B is the leader; release it, then press a command key. The _keymap()
table below supplies bindings and in-app F1 help. Notes: s saves Markdown;
r toggles the live preview. Components share one UI-agnostic App.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel,
                               QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget, QMenuBar)

from ..cli.app import App
from ..dictionaries import get_dictionary
from ..study.judge import get_judge
from ..study.session import StudySession
from . import theme
from .components import (ConsoleComponent, DocumentsComponent, NotesComponent,
                         VocabComponent)
from .context import WorkspaceContext
from .help_overlay import HelpOverlay
from .hotkeys import HotkeyFilter
from .study_view import StudyView
from .workspace import Workspace

COMPONENT_ORDER = ["vocab", "notes", "documents", "console"]


class MainWindow(QWidget):
    def __init__(self, root=None):
        super().__init__()
        self.app = App(root)
        self._bootstrap_dictionary()
        self.ctx = WorkspaceContext(self.app)
        self.ctx.study_requested.connect(self._open_study)
        self.ctx.view_requested.connect(self._open_viewer)
        self.ctx.add_word_requested.connect(self._add_highlighted_word)
        self.setWindowTitle("Vocab Study")
        self.resize(1040, 700)
        self._build()
        # Keep a reference: Qt event filters must remain alive for key delivery.
        self.hotkeys = HotkeyFilter(self)
        QApplication.instance().installEventFilter(self.hotkeys)
        self._install_shortcuts()
        # keep the pane highlight in sync with the REAL keyboard focus, whether
        # it moves by click or by keyboard (fixes "focus only changes on click")
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        self.ctx.changed.connect(self._refresh_modules)
        self.ctx.module_changed.connect(self._refresh_modules)
        self.ctx.log.connect(self._show_message)
        self._refresh_modules()
        if self.app.migration_note:
            self.ctx.log.emit(self.app.migration_note)
        self.ctx.log.emit("Shortcuts (tmux-style): press Ctrl+B, then a key (e.g. v split, z zoom, "
                          "x close). Press F1 for the full list.")

    def _bootstrap_dictionary(self) -> None:
        # Offline data is bundled by the desktop build. Source users may install
        # it explicitly; startup must not wait for a download.
        pass

    def _show_message(self, text):
        self.status.setText(text)
        self.status.setToolTip(text)

    # ---- component factory ---------------------------------------------
    def _make_component(self, key: str):
        if key == "vocab":
            return VocabComponent(self.ctx)
        if key == "notes":
            return NotesComponent(self.ctx)
        if key == "documents":
            return DocumentsComponent(self.ctx)
        if key == "console":
            return ConsoleComponent(self.ctx)
        return VocabComponent(self.ctx)

    # ---- layout ---------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        menu = QMenuBar(self)
        outer.addWidget(menu)
        notes_menu = menu.addMenu("Notes")
        save_action = notes_menu.addAction("Save focused note", self._save_focused_note)
        save_action.setShortcut("Ctrl+S")
        notes_menu.addAction("Toggle preview", self._toggle_note_preview)
        view_menu = menu.addMenu("View")
        for key in COMPONENT_ORDER:
            view_menu.addAction(key.title(), lambda checked=False, k=key:
                                self.workspace.set_focused_component(k))
        view_menu.addSeparator()
        view_menu.addAction("Split side by side", lambda: self.workspace.split_vertical())
        view_menu.addAction("Split stacked", lambda: self.workspace.split_horizontal())
        view_menu.addAction("Zoom pane", lambda: self.workspace.toggle_zoom())
        view_menu.addAction("Close pane", lambda: self.workspace.close_focused())
        view_menu.addAction("Toggle sidebar", self._toggle_sidebar)
        menu.addAction("Help", self._toggle_help)
        body = QHBoxLayout()
        body.setContentsMargins(12, 12, 12, 6)
        body.setSpacing(12)
        outer.addLayout(body, 1)

        # sidebar (collapsible)
        self.sidebar = QWidget()
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(0, 0, 0, 0)
        side.setSpacing(8)
        side.addWidget(QLabel("MODULES", objectName="Muted"))
        self.module_list = QListWidget()
        self.module_list.currentTextChanged.connect(self._on_module_selected)
        side.addWidget(self.module_list, 1)
        newb = QPushButton("+ New module"); newb.clicked.connect(self._new_module)
        delb = QPushButton("Delete module"); delb.setObjectName("Bad"); delb.clicked.connect(self._delete_module)
        studyb = QPushButton("Study ▶"); studyb.clicked.connect(lambda: self._open_study([self.app.current_module] if self.app.current_module else []))
        allb = QPushButton("Study All"); allb.clicked.connect(lambda: self._open_study(None))
        for b in (newb, delb, studyb, allb):
            side.addWidget(b)
        self.sidebar.setFixedWidth(210)
        body.addWidget(self.sidebar)

        # workspace
        self.workspace = Workspace(self._make_component, COMPONENT_ORDER)
        body.addWidget(self.workspace, 1)

        # status / keyhint bar
        self.status = QLabel("", objectName="Muted")
        self.status.setContentsMargins(14, 4, 14, 8)
        outer.addWidget(self.status)

    # ---- shortcuts ------------------------------------------------------
    def _keymap(self):
        """Single source of truth. Each row:
            (leader_letter, direct_chords, label, category, action)
        leader_letter: the plain key pressed AFTER Ctrl+B (release the prefix).
        direct_chords: optional list of
        (mods,key,display) also bound directly (used for arrows/help that rarely
        conflict). Either may be None.
        """
        ws = self.workspace
        CS = Qt.ControlModifier | Qt.ShiftModifier
        N = Qt.NoModifier
        return [
            # leader, direct, label, category, action
            ("/", [(N, Qt.Key_F1, "F1")], "Show / hide this help", "General", self._toggle_help),
            ("b", None, "Toggle module sidebar", "General", self._toggle_sidebar),
            ("m", None, "Focus module list", "General", self._focus_module_list),
            ("s", None, "Save focused note", "General", self._save_focused_note),
            ("r", None, "Toggle live Markdown preview", "Notes", self._toggle_note_preview),
            ("h", [(CS, Qt.Key_Left, "Ctrl+Shift+←")], "Focus pane left", "Move between panes", lambda: ws.move_focus("h")),
            ("j", [(CS, Qt.Key_Down, "Ctrl+Shift+↓")], "Focus pane down", "Move between panes", lambda: ws.move_focus("j")),
            ("k", [(CS, Qt.Key_Up, "Ctrl+Shift+↑")], "Focus pane up", "Move between panes", lambda: ws.move_focus("k")),
            ("l", [(CS, Qt.Key_Right, "Ctrl+Shift+→")], "Focus pane right", "Move between panes", lambda: ws.move_focus("l")),
            ("n", None, "Next component in pane", "Components", lambda: ws.cycle_component(1)),
            ("p", None, "Previous component in pane", "Components", lambda: ws.cycle_component(-1)),
            ("v", None, "Split pane vertically (side by side)", "Panes", ws.split_vertical),
            ("-", None, "Split pane horizontally (stacked)", "Panes", ws.split_horizontal),
            ("z", None, "Zoom / un-zoom focused pane", "Panes", ws.toggle_zoom),
            ("x", None, "Close focused pane", "Panes", ws.close_focused),
            ("1…9", None, "Focus pane by number", "Move between panes", None),
        ]

    LEADER_DISPLAY = "Ctrl+B"

    def _install_shortcuts(self) -> None:
        self.hotkeys.set_leader(Qt.ControlModifier, Qt.Key_B)
        self.hotkeys.set_arm_callback(self._on_leader_arm)
        for leader, direct, _label, _cat, action in self._keymap():
            if action is None:
                continue
            if leader and len(leader) == 1:
                # register the plain key (letters use Key_<upper>; symbols by code)
                key = self._key_for_leader_char(leader)
                if key is not None:
                    self.hotkeys.bind_leader_key(key, action)
            for mods, key, _disp in (direct or []):
                self.hotkeys.bind_direct(mods, key, action)
        # leader digits 1..9 focus pane N
        for n in range(1, 10):
            self.hotkeys.bind_leader_key(getattr(Qt, f"Key_{n}"),
                                         lambda n=n: self.workspace.focus_index(n))

    @staticmethod
    def _key_for_leader_char(ch: str):
        if ch.isalpha():
            return getattr(Qt, f"Key_{ch.upper()}")
        specials = {"-": Qt.Key_Minus, "?": Qt.Key_Question, "/": Qt.Key_Slash}
        return specials.get(ch)

    def _on_leader_arm(self, armed: bool) -> None:
        if armed:
            self.status.setText("LEADER: press a key  (b sidebar · v/- split · z zoom · "
                                "x close · h/j/k/l move · n/p cycle · r preview · / help · Esc cancel)")
        else:
            self._refresh_status()

    # ---- sidebar / modules ---------------------------------------------
    def _toggle_sidebar(self) -> None:
        self.sidebar.setVisible(not self.sidebar.isVisible())

    def _focus_module_list(self) -> None:
        if not self.sidebar.isVisible():
            self.sidebar.setVisible(True)
        self.module_list.setFocus()
        if self.module_list.count() and self.module_list.currentRow() < 0:
            self.module_list.setCurrentRow(0)

    def _refresh_modules(self) -> None:
        self.module_list.blockSignals(True)
        self.module_list.clear()
        for name in self.app.storage.list_names():
            wl = self.app.storage.load_words(name)
            n_notes = len(self.app.storage.note_names(name))
            item = QListWidgetItem(f"{name}  ({len(wl.words)}w · {n_notes}n)")
            item.setData(Qt.UserRole, name)
            self.module_list.addItem(item)
            if name == self.app.current_module:
                self.module_list.setCurrentItem(item)
        self.module_list.blockSignals(False)
        self._refresh_status()

    def _on_module_selected(self, _text: str) -> None:
        item = self.module_list.currentItem()
        if not item:
            return
        name = item.data(Qt.UserRole)
        try:
            self.app.cmd_use(name)
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
            return
        self.ctx.module_changed.emit(name)
        self._refresh_status()

    def _new_module(self) -> None:
        name, ok = QInputDialog.getText(self, "New module", "Module name:")
        if not ok or not name.strip():
            return
        try:
            self.ctx.log.emit(self.app.cmd_create(name.strip()))
            self.app.cmd_use(name.strip())
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
        self._refresh_modules()
        self.ctx.module_changed.emit(self.app.current_module or "")

    def _delete_module(self) -> None:
        name = self.app.current_module
        if not name:
            return
        if QMessageBox.question(self, "Delete module",
                                f"Delete '{name}' and ALL its contents?") != QMessageBox.Yes:
            return
        try:
            self.ctx.log.emit(self.app.cmd_delete(name))
            self.ctx.notes.discard(name)
        except Exception as e:
            self.ctx.log.emit(f"! {e}")
        self._refresh_modules()
        self.ctx.module_changed.emit(self.app.current_module or "")

    def _refresh_status(self) -> None:
        cur = self.app.current_module or "—"
        dic, _ = self.app.effective_dictionary()
        due = "—"
        if self.app.current_module:
            import time as _t
            stats = self.app.storage.load_stats(self.app.current_module)
            due = str(len(self.app.scheduler.due_order(stats, _t.time())))
        net = "offline" if not dic.requires_network else "ONLINE"
        z = "  ·  ZOOM" if self.workspace.is_zoomed else ""
        sense = f"    ·    sense: {self.app.config.disambiguator}"
        self.status.setText(
            f"module: {cur}    ·    due: {due}    ·    dict: {dic.id} ({net}){sense}{z}")

    # ---- help panel -----------------------------------------------------
    def _toggle_help(self) -> None:
        existing = getattr(self, "_help", None)
        if existing is not None and existing.isVisible():
            existing.hide()
            return
        if existing is None:
            self._help = HelpOverlay(self._keymap(), parent=self)
        self._help.place(self.rect())
        self._help.show()
        self._help.raise_()
        # deliberately do NOT setFocus() — the panel must not steal keyboard
        # focus, so global hotkeys keep firing while it stays open

    # ---- focus sync -----------------------------------------------------
    def _on_focus_changed(self, _old, new) -> None:
        if new is not None:
            self.workspace.sync_focus_to_widget(new)
        self._refresh_status()

    # ---- notes save ----------------------------------------------------
    def _save_focused_note(self) -> None:
        pane = self.workspace.focused
        if pane and isinstance(pane.component, NotesComponent):
            pane.component.save()

    def _toggle_note_preview(self) -> None:
        pane = self.workspace.focused
        if pane and isinstance(pane.component, NotesComponent):
            pane.component.toggle_preview()

    # ---- study ----------------------------------------------------------
    def _open_study(self, names) -> None:
        """names: list of module names, or None for STUDYALL. Opens Study in a
        split beside the focused pane so notes/vocab stay visible."""
        if names is None:
            names = self.app.study_targets_all()
            label = "All modules"
        else:
            names = [n for n in names if n]
            label = ", ".join(names) if names else None
        if not names:
            self.ctx.log.emit("! No module selected to study.")
            return
        mechanical = self.app.config.mechanical_repetition
        judge = get_judge(self.app.config.judge_backend) if mechanical else None
        try:
            session = StudySession(self.app.storage, self.app.scheduler, names,
                                   mechanical=mechanical, judge=judge, session_label=label)
        except Exception as error:
            self.ctx.log.emit(f"! {error}")
            return
        if session.total == 0:
            self.ctx.log.emit("Nothing due right now. Add more words, or come back later.")
            return
        view = StudyView(session)
        view.finished.connect(lambda summary, source=view: self._on_study_finished(summary, source))
        # split beside the focused pane and drop Study into the new pane
        self.workspace.split_vertical()
        self.workspace.focused.set_component(view, "study")
        self.workspace.focused.title.setText("Study")
        self.workspace.focused.set_focused(True)
        self._refresh_status()

    def _on_study_finished(self, summary: str, source=None) -> None:
        self.ctx.log.emit(summary)
        # turn the study pane back into vocab
        pane = next((p for p in self.workspace._panes if p.component is source), None)
        if pane is not None:
            pane.set_component(self._make_component("vocab"), "vocab")
            pane.set_focused(True)
        self.ctx.changed.emit()
        self._refresh_modules()

    def _open_viewer(self, path: str) -> None:
        """Open a PDF/EPUB in an in-app viewer, REPLACING the current (Documents)
        pane. A '‹ Documents' button in the viewer restores the Documents view."""
        from .viewers import make_viewer
        try:
            viewer = make_viewer(path, self.ctx)
        except Exception as e:
            self.ctx.log.emit(f"! Could not open document: {e}")
            return
        if viewer is None:
            self.ctx.log.emit("! No in-app viewer for that file type.")
            return
        pane = self.workspace.focused
        if pane is None:
            self.workspace.split_vertical()
            pane = self.workspace.focused
        viewer.back_requested.connect(lambda p=pane: self._close_viewer(p))
        pane.set_component(viewer, "viewer")
        pane.title.setText(getattr(viewer, "TITLE", "Document"))
        pane.set_focused(True)
        self.ctx.log.emit(f"Opened {os.path.basename(path)} in this pane.")
        self._refresh_status()

    def _close_viewer(self, pane) -> None:
        """Restore a viewer pane back to the Documents component."""
        pane.set_component(self._make_component("documents"), "documents")
        pane.title.setText("Documents")
        pane.set_focused(True)
        self._refresh_status()

    def _add_highlighted_word(self, word: str, sentence: str, module: str = "") -> None:
        """A reader highlighted a word — add it to the current module's vocab,
        using the sentence for in-context sense selection."""
        if not (module or self.app.current_module):
            self.ctx.log.emit("! Open or select a module before adding words.")
            return
        try:
            self.ctx.add_word(word, sentence=sentence, target=module or None)
        except Exception as e:
            self.ctx.log.emit(f"! Could not add '{word}': {e}")


    # ---- keep status live on any focus change --------------------------
    def focusInEvent(self, e):
        self._refresh_status()
        super().focusInEvent(e)

    def closeEvent(self, e):
        if self.ctx.busy:
            QMessageBox.information(self, "Lookup in progress",
                                    "Please let the current word lookup finish before closing.")
            e.ignore()
            return
        drafts = self.ctx.notes.dirty_buffers()
        if drafts:
            names = "\n".join(f"• {b.module}/{b.note}" for b in drafts)
            choice = QMessageBox.question(
                self, "Unsaved notes", f"Save changes before closing?\n\n{names}",
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
                QMessageBox.Save)
            if choice == QMessageBox.Cancel:
                e.ignore()
                return
            if choice == QMessageBox.Save:
                try:
                    for buffer in drafts:
                        buffer.save()
                except Exception as error:
                    QMessageBox.warning(self, "Could not save notes", str(error))
                    e.ignore()
                    return
        QApplication.instance().removeEventFilter(self.hotkeys)
        # persist reading position for any open document viewer before quitting
        for pane in list(self.workspace._panes):
            saver = getattr(pane.component, "save_position", None)
            if callable(saver):
                try:
                    saver()
                except Exception:
                    pass
        super().closeEvent(e)

    def resizeEvent(self, e):
        # keep the help panel docked on the right edge when visible
        h = getattr(self, "_help", None)
        if h is not None and h.isVisible():
            h.place(self.rect())
        super().resizeEvent(e)
