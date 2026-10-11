"""The desktop Modules menu and the Console back up and restore modules.

Native file and input dialogs cannot run offscreen, so their static
functions are replaced; the progress dialog, message boxes for replacing a
file and the Console's typed keys are real.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import zipfile
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QMenu, QMenuBar, QMessageBox,
                               QProgressDialog)

from .test_gui_reliability import qt, window  # noqa: F401  (fixtures)
from .test_console_commands import console, dirty_draft
from vocab.core import backup

DATE = "2026-10-10"


@pytest.fixture(autouse=True)
def frozen_date(monkeypatch):
    import datetime
    monkeypatch.setattr(backup, "_today", lambda: datetime.date(2026, 10, 10))


class Dialogs:
    """Records the file dialogs the app opens and answers them."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.answers = {}
        for name in ("getSaveFileName", "getOpenFileNames", "getExistingDirectory"):
            monkeypatch.setattr(QFileDialog, name, self._answer(name))

    def _answer(self, name):
        def answer(parent, title, start="", *rest, **kwargs):
            self.calls.append((name, title, start))
            return self.answers.get(name, ("", "") if name != "getExistingDirectory" else "")
        return answer


def message(window):
    return window._message_text


def listed(window):
    """Module names in the sidebar."""
    return [window.module_list.item(i).data(Qt.UserRole)
            for i in range(window.module_list.count())]


def test_the_modules_menu_comes_first(window):
    menus = [action.text() for action in window.findChild(QMenuBar).actions()]
    assert menus[0] == "Modules"
    modules = next(m for m in window.findChild(QMenuBar).findChildren(QMenu)
                   if m.title() == "Modules")
    titles = [a.text() for a in modules.actions()]
    assert titles == ["Back up current module…", "Back up all modules…", "Restore from backup…",
                      "", "Folder for files…"]


def test_back_up_current_module_from_the_menu(window, tmp_path, monkeypatch, documents_folder):
    dialogs = Dialogs(monkeypatch)
    dest = tmp_path / "x.zip"
    dialogs.answers["getSaveFileName"] = (str(dest), "")
    window.backup_action.trigger()
    assert dialogs.calls == [("getSaveFileName", "Back up module",
                              str(documents_folder / f"Book {DATE}.zip"))]
    assert message(window).startswith(f"Backed up 'Book' -> {dest} (0 words, 1 note, ")
    assert backup.read_backup(dest).module == "Book"
    assert window.app.cmd_restore(str(dest), "Copy").startswith("Restored module 'Copy'")
    # Without a module there is nothing to back up, and no dialog.
    window.app.current_module = None
    dialogs.calls.clear()
    window.backup_action.trigger()
    assert message(window) == "! Select or create a module first."
    assert dialogs.calls == []


def test_the_menu_never_replaces_a_file_the_dialog_did_not_ask_about(window, tmp_path,
                                                                     monkeypatch):
    # The dialog returns "x"; the backup is x.zip, which exists. Qt asked
    # about "x" only, so the app asks about x.zip.
    dialogs = Dialogs(monkeypatch)
    dialogs.answers["getSaveFileName"] = (str(tmp_path / "x"), "")
    existing = tmp_path / "x.zip"
    existing.write_bytes(b"an older backup")
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a: asked.append(text) or QMessageBox.No)
    window.backup_action.trigger()
    assert asked == [f"{existing} already exists. Replace it?"]
    assert existing.read_bytes() == b"an older backup"
    assert message(window) == "Cancelled; nothing was written."
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window.backup_action.trigger()
    assert backup.read_backup(existing).module == "Book"
    # A file the dialog itself confirmed is replaced without asking again.
    dialogs.answers["getSaveFileName"] = (str(existing), "")
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a: pytest.fail("asked twice about the same file"))
    existing.write_bytes(b"older again")
    window.backup_action.trigger()
    assert backup.read_backup(existing).module == "Book"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".partial")] == []


def test_backup_warns_about_unsaved_notes(window, tmp_path, monkeypatch):
    dirty_draft(window, text="unsaved draft")
    dialogs = Dialogs(monkeypatch)
    dest = tmp_path / "x.zip"
    dialogs.answers["getSaveFileName"] = (str(dest), "")
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a: asked.append(text) or QMessageBox.No)
    window.backup_action.trigger()
    assert asked == ["'Book' has unsaved changes in the note One. The backup will hold their "
                     "saved versions. Back up anyway?"]
    assert dialogs.calls == [] and not dest.exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    window.backup_action.trigger()
    with zipfile.ZipFile(dest) as archive:
        assert archive.read("module/notes/One.md") == b"original"  # the saved text
        assert not any("drafts" in name for name in archive.namelist())
    assert "Not included: recovery copies of unsaved changes to the note 'One'" in message(window)


def test_restore_from_the_menu_asks_for_a_name_on_conflict(window, tmp_path, monkeypatch):
    good = tmp_path / "Book backup.zip"
    window.app.cmd_backup("Book", str(good))
    corrupt = tmp_path / "corrupt.zip"
    corrupt.write_bytes(b"not a zip at all")
    dialogs = Dialogs(monkeypatch)
    dialogs.answers["getOpenFileNames"] = ([str(good), str(corrupt)], "")
    questions = []

    def name(parent, title, label, mode, text):
        questions.append((label, text))
        return "Book copy", True
    monkeypatch.setattr(QInputDialog, "getText", name)
    logged = []
    window.ctx.log.connect(logged.append)
    window.restore_action.trigger()
    assert questions == [(f"A module named 'Book' already exists. Restore '{good.name}' as:",
                          "Book (restored)")]
    assert any(line.startswith("Restored module 'Book copy' from ") for line in logged)
    assert logged[-1] == (f"! Cannot restore {corrupt}: it is not a ZIP file, or it is damaged. "
                          "Nothing was restored.")
    assert "Book copy" in listed(window)
    assert window.app.current_module == "Book"
    # Cancelling the name skips that backup.
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("", False))
    dialogs.answers["getOpenFileNames"] = ([str(good)], "")
    before = window.app.storage.list_names()
    window.restore_action.trigger()
    assert window.app.storage.list_names() == before
    assert message(window) == f"Skipped {good}; nothing was restored."


def test_back_up_all_from_the_menu(window, tmp_path, monkeypatch):
    window.app.cmd_create("Second")
    dialogs = Dialogs(monkeypatch)
    dialogs.answers["getExistingDirectory"] = str(tmp_path)
    window.backup_all_action.trigger()
    folder = tmp_path / f"Vocab Study backup {DATE}"
    assert sorted(p.name for p in folder.iterdir()) == ["Book.zip", "Second.zip"]
    assert message(window).startswith(f"Backed up 2 of 2 modules -> {folder}")


def test_cancel_in_the_progress_dialog_leaves_nothing(window, tmp_path, monkeypatch):
    dialogs = Dialogs(monkeypatch)
    archive = tmp_path / "kept.zip"
    window.app.cmd_backup("Book", str(archive))
    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)
    dialogs.answers["getSaveFileName"] = (str(tmp_path / "x.zip"), "")
    window.backup_action.trigger()
    assert message(window) == "Backup cancelled; nothing was written."
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix in (".zip", ".partial")) == \
        ["kept.zip"]
    dialogs.answers["getOpenFileNames"] = ([str(archive)], "")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("Copy", True))
    window.restore_action.trigger()
    assert message(window) == "Restore cancelled; nothing was restored."
    assert window.app.storage.list_names() == ["Book"]
    assert not any(p.name.startswith(".vocab-staging-")
                   for p in window.app.paths.modules_dir.iterdir())
    dialogs.answers["getExistingDirectory"] = str(tmp_path)
    window.backup_all_action.trigger()
    assert message(window) == "Backup cancelled; nothing was written."
    assert not (tmp_path / f"Vocab Study backup {DATE}").exists()
    assert window.findChildren(QProgressDialog) == [] or all(
        not d.isVisible() for d in window.findChildren(QProgressDialog))


def test_cancel_stops_a_restore_of_several_backups(window, tmp_path, monkeypatch):
    from vocab.gui import backup_actions
    names = ["First", "Second", "Third"]
    archives = [tmp_path / f"{name}.zip" for name in names]
    for name, archive in zip(names, archives):
        window.app.cmd_create(name)
        window.app.cmd_backup(name, str(archive))
        window.app.storage.delete(name)
    dialogs = Dialogs(monkeypatch)
    dialogs.answers["getOpenFileNames"] = ([str(a) for a in archives], "")
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: pytest.fail("no name is taken"))
    logged = []
    window.ctx.log.connect(logged.append)
    real = backup_actions.ProgressCallback.__call__
    labels = []

    def cancel_when(test):
        def press_cancel(self, done, total, label=""):
            labels.append(label)
            real(self, done, total, label)
            if test(done, total, label):
                self.dialog.cancel()  # what the Cancel button does
        monkeypatch.setattr(backup_actions.ProgressCallback, "__call__", press_cancel)

    # Cancel while the second backup is restored: the third is not restored.
    cancel_when(lambda done, total, label: label == "Restoring 'Second'")
    window.restore_action.trigger()
    assert logged[0].startswith(f"Restored module 'First' from {archives[0]} (")
    assert logged[1:] == ["Restore cancelled; nothing was restored.",
                          "Cancelled after restoring 1 of 3 backups: First.zip. Not restored: "
                          "Second.zip, Third.zip."]
    assert "Restoring 'Third'" not in labels
    assert window.app.storage.list_names() == ["Book", "First"]
    assert "First" in listed(window)
    # Cancel pressed after the second's last step: it is complete, the third
    # still is not restored.
    window.app.storage.delete("First")
    logged.clear()
    labels.clear()
    cancel_when(lambda done, total, label: label == "Restoring 'Second'" and done == total)
    window.restore_action.trigger()
    assert [line.split(" from ")[0] for line in logged[:2]] == [
        "Restored module 'First'", "Restored module 'Second'"]
    assert logged[2:] == ["Cancelled after restoring 2 of 3 backups: First.zip, Second.zip. "
                          "Not restored: Third.zip."]
    assert "Restoring 'Third'" not in labels
    assert window.app.storage.list_names() == ["Book", "First", "Second"]
    # Cancel with one backup left says nothing more than that restore's reply.
    window.app.storage.delete("Second")
    logged.clear()
    dialogs.answers["getOpenFileNames"] = ([str(archives[1])], "")
    cancel_when(lambda done, total, label: True)
    window.restore_action.trigger()
    assert logged == ["Restore cancelled; nothing was restored."]
    assert window.app.storage.list_names() == ["Book", "First"]


def test_console_backup_restore_and_folder_by_typed_keys(window, tmp_path, monkeypatch, capsys,
                                                         documents_folder, qt):
    archive = documents_folder / f"Book {DATE}.zip"
    log = console(window, "BACKUP Book")
    assert f"Backed up 'Book' -> {archive} (" in log
    log = console(window, f'RESTORE "{archive}" AS Copy')
    assert f"Restored module 'Copy' from {archive}" in log
    qt.processEvents()
    assert "Copy" in listed(window)
    assert f"start in {documents_folder} (your Documents folder)" in console(window, "FOLDER")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    log = console(window, f'BACKUP Book "{archive}"')
    assert log.rstrip().endswith("Cancelled; nothing was written.")
    # Unsaved notes are mentioned as from the menu.
    dirty_draft(window, text="unsaved draft")
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda parent, title, text, *a: asked.append(text) or QMessageBox.No)
    log = console(window, "BACKUP Book")
    assert asked and "unsaved changes in the note One" in asked[0]
    assert log.rstrip().endswith("Cancelled; nothing was written.")
    assert not (documents_folder / f"Book {DATE} (2).zip").exists()
    out = capsys.readouterr()
    assert "Backed up" not in out.out and "Backing up" not in out.out


def test_folder_for_files_menu_sets_the_setting(window, tmp_path, monkeypatch, documents_folder):
    dialogs = Dialogs(monkeypatch)
    chosen = tmp_path / "USB"
    chosen.mkdir()
    dialogs.answers["getExistingDirectory"] = str(chosen)
    window.files_folder_action.trigger()
    assert dialogs.calls[0][2] == str(documents_folder)  # starts in the current folder
    assert message(window) == f"Relative file paths now start in {chosen}."
    assert window.app.config.files_folder == str(chosen)
    dialogs.answers["getExistingDirectory"] = ""  # cancelled
    window.files_folder_action.trigger()
    assert window.app.config.files_folder == str(chosen)


def test_the_progress_dialog_names_the_work_and_closes(window, tmp_path, monkeypatch, qt):
    from vocab.gui import backup_actions
    monkeypatch.setattr(backup_actions.ProgressCallback, "MIN_DURATION", 0)
    monkeypatch.setattr(backup, "CHUNK", 4096)
    source = tmp_path / "big.pdf"
    source.write_bytes(os.urandom(256 << 10))
    window.app.storage.add_document("Book", source)
    seen = []
    real = backup_actions.ProgressCallback.__call__

    def spy(self, done, total, label=""):
        real(self, done, total, label)
        seen.append((self.dialog.isVisible(), self.dialog.labelText(), self.dialog.maximum(),
                     self.dialog.value(), self.dialog.isModal() or
                     self.dialog.windowModality() == Qt.WindowModal))
    monkeypatch.setattr(backup_actions.ProgressCallback, "__call__", spy)
    dialogs = Dialogs(monkeypatch)
    dialogs.answers["getSaveFileName"] = (str(tmp_path / "x.zip"), "")
    window.backup_action.trigger()
    assert message(window).startswith("Backed up 'Book'")
    assert seen and all(modal for *_, modal in seen)
    assert any(visible for visible, *_ in seen)
    assert {label for _, label, *_ in seen} == {"Backing up 'Book'"}
    assert seen[-1][3] == seen[-1][2]  # finished at 100%
    qt.processEvents()
    assert not any(d.isVisible() for d in window.findChildren(QProgressDialog))
