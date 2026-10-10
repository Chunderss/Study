"""Messages stay readable: the status refresh on focus changes never replaces
them, and a Console opened later lists the earlier ones."""
import json
import shutil

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMessageBox

from .test_gui_reliability import qt, window  # noqa: F401
from .test_migration_recovery import legacy_list
from vocab.cli.app import App
from vocab.core.paths import get_paths
from vocab.gui.components import ConsoleComponent
from vocab.gui.main_window import MainWindow

HINT = "Shortcuts (tmux-style)"
RECOVERED = "Recovered 1 unsaved note draft(s)"


def on_screen(win, text):
    return any(text in label.text() for label in win.findChildren(QLabel) if label.isVisible())


def churn(win, qt):
    """Show the window and move keyboard focus about, as using it does."""
    win.show()
    win.activateWindow()
    for widget in (win.module_list, win.workspace.focused.component, win.module_list):
        qt.processEvents()
        if hasattr(widget, "focus_default"):
            widget.focus_default()
        else:
            widget.setFocus()
        QTest.qWait(20)
    assert qt.focusWidget() is win.module_list  # focus really moved


def open_console(win):
    win.workspace.set_focused_component("console")
    console = win.workspace.focused.component
    assert isinstance(console, ConsoleComponent)
    return console


def book_with_draft(home):
    app = App(root=home)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.storage.save_note("Book", "One", "saved")
    app.storage.save_note_draft("Book", "One", "saved", "unsaved draft")


@pytest.fixture
def launch(qt, monkeypatch):
    opened = []

    def start(home):
        win = MainWindow(root=home)
        opened.append(win)
        churn(win, qt)
        return win
    yield start
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Discard)
    for win in opened:
        win.close()
        win.deleteLater()
    qt.processEvents()


def test_startup_notices_outlast_focus_changes_and_reach_a_later_console(launch, tmp_path):
    book_with_draft(tmp_path)
    (tmp_path / "workspace.json").write_text('{"schema": 1, "workspace": 5}', encoding="utf-8")
    win = launch(tmp_path)
    # The notice about unsaved work comes last, and says another one is listed.
    assert on_screen(win, f"{RECOVERED}. Open Notes to review and save them.  (+1 more: View → Console)")
    assert win.status.text().startswith("module: Book    ·    due: 0")
    log = open_console(win).out.toPlainText()
    for text in (RECOVERED, "Could not restore the previous workspace", HINT):
        assert log.count(text) == 1


def test_a_restored_console_gets_startup_notices_once(launch, tmp_path):
    book_with_draft(tmp_path)
    (tmp_path / "workspace.json").write_text(json.dumps({
        "schema": 1, "module": "Book",
        "workspace": {"tree": {"direction": "h", "sizes": [0], "children": [
            {"pane": {"component": "console"}, "order": 0}]}, "focused": 0, "zoomed": False},
    }), encoding="utf-8")
    win = launch(tmp_path)
    assert on_screen(win, RECOVERED) and not on_screen(win, "more: View → Console")
    console = win.workspace.focused.component
    assert isinstance(console, ConsoleComponent)
    log = console.out.toPlainText()
    assert log.count(RECOVERED) == 1 and log.count(HINT) == 1


def test_a_lone_migration_notice_is_not_hidden_by_the_shortcut_hint(launch, tmp_path):
    legacy_list(get_paths(tmp_path))
    win = launch(tmp_path)
    assert on_screen(win, "Migrated 1 list(s)")
    assert "Migrated 1 list(s)" in open_console(win).out.toPlainText()


def test_study_summary_stays_on_screen_and_due_counts_update(window, qt):
    window.app.cmd_add("word", target="Book", manual_def="meaning")
    window._open_study(["Book"])
    window.activateWindow()
    qt.processEvents()
    view = window.workspace.focused.component
    summaries = []
    view.finished.connect(summaries.append)
    for key in (Qt.Key_Space, Qt.Key_J):  # reveal, then Got it: the only card
        QTest.keyClick(qt.focusWidget(), key)
        qt.processEvents()
    assert summaries == ["Session done: 1/1 correct (100%)."]
    churn(window, qt)
    assert on_screen(window, summaries[0])
    assert "due: 0" in window.status.text()
    assert summaries[0] in open_console(window).out.toPlainText()


def test_a_resolved_problem_leaves_the_message_line(window, qt):
    window.app.cmd_create("Other")
    window.ctx.changed.emit()
    window.activateWindow()
    shutil.rmtree(window.app.paths.module_dir("Book"))
    # Focus changes notice the removed module. The module list goes first:
    # taking focus once it lists no current module, it would select "Other".
    for widget in (window.module_list, window.workspace.focused.component):
        widget.setFocus()
        QTest.qWait(20)
    assert window.app.current_module is None
    assert on_screen(window, "Module 'Book' is no longer available")
    window.module_list.setCurrentRow(0)
    assert window.app.current_module == "Other"
    assert window.status.text().startswith("module: Other")
    assert not on_screen(window, "is no longer available")


def test_a_console_opened_later_shows_recent_messages_once(window):
    for n in range(300):
        window.ctx.log.emit(f"message {n}")
    console = open_console(window)
    lines = console.out.toPlainText().splitlines()
    assert lines.count("message 299") == 1
    assert "message 0" not in lines  # the history is bounded: the oldest go first
    window.ctx.log.emit("after opening")
    assert console.out.toPlainText().splitlines().count("after opening") == 1


def test_a_message_of_several_lines_keeps_the_layout(window, qt):
    def sizes():
        qt.processEvents()
        return window.workspace.height(), window.height(), window.minimumSizeHint().height()
    before = sizes()
    window.ctx.add_word("other", manual="another meaning")  # "Added ..." and its sense
    assert on_screen(window, "Added 'other'") and sizes() == before
    window.ctx.log.emit("\n".join(f"line {n}" for n in range(40)))
    assert on_screen(window, "line 0  ·  line 1  ·  line 2") and sizes() == before
    assert "line 39" in window.message.toolTip()


def test_a_long_message_does_not_widen_the_window(window, qt):
    width = window.width()
    text = "! Could not save your place in " + "/folder" * 400 + "/book.epub"
    window.ctx.log.emit(text)
    qt.processEvents()
    assert window.width() == width
    assert window.message.toolTip() == f"View → Console lists recent messages.\n\n{text}"
