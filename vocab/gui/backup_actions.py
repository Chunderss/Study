"""The Modules menu: back up and restore modules, and the folder for files.

The work runs on the GUI thread under a modal progress dialog, Qt's idiom for
a long synchronous task: the dialog keeps the window responsive enough to
repaint and to press Cancel, and blocks everything else in the window (no
module deletion or closing mid-operation). The core never calls progress
while it holds the data folder's write lock, so timers that run meanwhile
(recovery copies of notes, status refreshes) can still save. Results and
errors are the terminal's own messages, logged through the context.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QLineEdit, QMessageBox,
                               QProgressDialog)

from ..core import backup
from ..core.backup import BackupExists, Cancelled
from ..core.paths import sanitize_module_name
from ..core.storage import _leftover_kind

FILTER = "Vocab Study backup (*.zip)"


class ProgressCallback:
    """progress(done, total, label) for the core, shown in one modal
    QProgressDialog created on first use. Cancel raises backup.Cancelled
    from the next call. ``cancelled`` stays true once Cancel was pressed,
    even when the work had already passed its last progress call."""

    MIN_DURATION = 500  # ms before the dialog appears; quick operations show none

    def __init__(self, parent, title="Vocab Study"):
        self.parent = parent
        self.title = title
        self.dialog = None
        self.cancelled = False

    def __call__(self, done, total, label=""):
        if self.dialog is None:
            self.dialog = QProgressDialog(label, "Cancel", 0, 1, self.parent)
            self.dialog.setWindowTitle(self.title)
            self.dialog.setWindowModality(Qt.WindowModal)
            self.dialog.setMinimumDuration(self.MIN_DURATION)
            self.dialog.setAutoClose(False)
            self.dialog.setAutoReset(False)
        if self.dialog.labelText() != label:
            self.dialog.setLabelText(label)
        # In KB, so a large total fits Qt's int range.
        maximum = max(1, total // 1024)
        if self.dialog.maximum() != maximum:
            self.dialog.setMaximum(maximum)
        self.dialog.setValue(min(maximum, done // 1024))  # processes events while modal
        if self.dialog.wasCanceled():
            self.cancelled = True
            raise Cancelled()

    def close(self):
        if self.dialog is not None:
            if self.dialog.wasCanceled():  # reset() forgets it
                self.cancelled = True
            self.dialog.reset()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None


def progress_callback(parent, title="Vocab Study") -> ProgressCallback:
    return ProgressCallback(parent, title)


def unsaved_notes(ctx, modules=None):
    """(module, note) of open notes with unsaved changes, in ``modules``
    (all modules when None)."""
    return [(b.module, b.note) for b in ctx.notes.dirty_buffers()
            if modules is None or b.module in modules]


def confirm_unsaved(parent, ctx, modules=None) -> bool:
    """Ask before backing up modules whose open notes have unsaved changes;
    the backup holds their saved versions. True to go ahead."""
    dirty = unsaved_notes(ctx, modules)
    if not dirty:
        return True
    if modules is not None and len(modules) == 1:
        which = "note" if len(dirty) == 1 else "notes"
        text = (f"'{modules[0]}' has unsaved changes in the {which} "
                f"{', '.join(note for _, note in dirty)}. The backup will hold their saved "
                "versions. Back up anyway?")
    else:
        text = (f"Notes have unsaved changes: {', '.join(f'{m}/{n}' for m, n in dirty)}. The "
                "backups will hold their saved versions. Back up anyway?")
    return QMessageBox.question(parent, "Unsaved notes", text, QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


def _ask_replace(parent, path) -> bool:
    return QMessageBox.question(parent, "Confirm", f"{path} already exists. Replace it?",
                                QMessageBox.Yes | QMessageBox.No,
                                QMessageBox.No) == QMessageBox.Yes


def _start_folder(window) -> Path:
    try:
        return window.app.files_folder()
    except ValueError as error:  # an unplugged folder: say so, start at home
        window.ctx.log.emit(f"! {error}")
        return Path.home()


def _run(window, title, work, progress=None):
    """Run work(progress) under a progress dialog (``progress``, or a new
    one); log its reply or error. Returns the reply, or None after an error."""
    progress = progress or progress_callback(window, title)
    try:
        message = work(progress)
    except Exception as error:
        window.ctx.log.emit(f"! {error}")
        return None
    finally:
        progress.close()
    if message:
        window.ctx.log.emit(message)
    return message


def back_up_current(window) -> None:
    app, ctx = window.app, window.ctx
    module = app.current_module
    if not module:
        ctx.log.emit("! Select or create a module first.")
        return
    try:
        module = app.storage.canonical_name(module)
    except Exception as error:
        ctx.log.emit(f"! {error}")
        return
    if not confirm_unsaved(window, ctx, [module]):
        return
    try:
        start, _auto = app.backup_destination(module)
    except ValueError as error:
        ctx.log.emit(f"! {error}")
        start = backup.default_backup_path(Path.home(), module)
    path, _ = QFileDialog.getSaveFileName(window, "Back up module", str(start), FILTER)
    if not path:
        return
    chosen = Path(path)
    final = chosen if chosen.name.lower().endswith(".zip") else chosen.with_name(chosen.name + ".zip")
    # The dialog asked before replacing exactly the file it returned; any other
    # file (".zip" appended) is asked about here.
    replace = final == chosen and os.path.lexists(final)

    def work(progress):
        try:
            return app.cmd_backup(module, str(final), replace=replace, progress=progress)
        except BackupExists as exists:
            if not _ask_replace(window, exists.path):
                return "Cancelled; nothing was written."
            return app.cmd_backup(module, str(exists.path), replace=True, progress=progress)
    _run(window, f"Backing up {module}", work)


def back_up_all(window) -> None:
    app, ctx = window.app, window.ctx
    if not confirm_unsaved(window, ctx):
        return
    folder = QFileDialog.getExistingDirectory(window, "Back up all modules into",
                                              str(_start_folder(window)))
    if not folder:
        return
    _run(window, "Backing up all modules",
         lambda progress: app.cmd_backup_all(folder, progress=progress))


def _name_free(app, name) -> bool:
    try:
        name = sanitize_module_name(name)
    except ValueError:
        return False
    return not (_leftover_kind(name) or app.storage.exists(name)
                or os.path.lexists(app.paths.module_dir(name)))


def _suggestion(app, name) -> str:
    try:
        base = f"{sanitize_module_name(name)} (restored)"
    except (ValueError, TypeError, AttributeError):  # no usable name in the backup
        base = "Restored module"
    candidate, number = base, 2
    while not _name_free(app, candidate) and number < 100:
        candidate = f"{base[:-1]} {number})" if base.endswith(")") else f"{base} {number}"
        number += 1
    return candidate


def restore(window) -> None:
    """Restore one or more backups, each as a new module; a name that is
    taken asks for another, and cancelling that question skips the backup.
    Cancel in the progress dialog stops the whole batch: the backups after
    it are not restored, and the message says which were. The current module
    stays selected."""
    app, ctx = window.app, window.ctx
    paths, _ = QFileDialog.getOpenFileNames(window, "Restore modules from backups",
                                            str(_start_folder(window)),
                                            f"{FILTER};;All files (*)")
    restored = []
    for path in paths:
        try:
            full, info = app.backup_info(path)
        except ValueError as error:
            ctx.log.emit(f"! {error}")
            continue
        name, new_name = info.module, None
        if not (isinstance(name, str) and _name_free(app, name)):
            question = f"Restore '{full.name}' as:"
            if isinstance(name, str) and name.strip():
                try:
                    clean = sanitize_module_name(name)
                    taken = app.storage.exists(clean) or os.path.lexists(app.paths.module_dir(clean))
                except ValueError:
                    taken = False
                question = (f"A module named {backup.shown(name)} already exists. " if taken else
                            f"The backup's module name {backup.shown(name)} cannot be used here. "
                            ) + question
            text, ok = QInputDialog.getText(window, "Restore module", question, QLineEdit.Normal,
                                            _suggestion(app, name))
            if not ok or not text.strip():
                ctx.log.emit(f"Skipped {full}; nothing was restored.")
                continue
            new_name = text.strip()
        progress = progress_callback(window, "Restoring a module")
        reply = _run(window, "Restoring a module",
                     lambda progress: app.cmd_restore(str(full), new_name, progress=progress),
                     progress)
        if reply and reply.startswith("Restored module"):
            restored.append(path)
        if progress.cancelled:
            if len(paths) > 1 and len(restored) < len(paths):
                ctx.log.emit(_cancelled_batch(paths, restored))
            break
    if restored:
        ctx.changed.emit()


def _cancelled_batch(paths, restored) -> str:
    """The message after Cancel stopped a restore of several backups."""
    if not restored:
        return f"Cancelled; none of the {len(paths)} backups was restored."
    left = [Path(p).name for p in paths if p not in restored]
    return (f"Cancelled after restoring {len(restored)} of {len(paths)} backups: "
            f"{', '.join(Path(p).name for p in restored)}. Not restored: {', '.join(left)}.")


def choose_files_folder(window) -> None:
    folder = QFileDialog.getExistingDirectory(window, "Folder for files",
                                              str(_start_folder(window)))
    if not folder:
        return
    try:
        window.ctx.log.emit(window.app.cmd_folder(folder))
    except Exception as error:
        window.ctx.log.emit(f"! {error}")
