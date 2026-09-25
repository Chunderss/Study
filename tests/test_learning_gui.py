import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QPdfWriter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QDialog

from .test_markdown_preview import qt, notes
from .test_gui_reliability import window, select
from .test_learning import capture
from vocab.cli.app import App
from vocab.core.learning import LearningStore
from vocab.gui.context import WorkspaceContext
from vocab.gui.learning import CaptureDialog, ReviewDialog, LearningComponent
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import PdfViewer


def test_note_recovery_after_restart_keeps_saved_file_and_conflict_detection(notes):
    notes.editor.setPlainText("recover this unsaved thought")
    QTest.qWait(1150)
    storage = notes.app.storage
    assert storage.load_note("Book", "Chapter") == "# Original\n\nA note."
    assert storage.load_note_draft("Book", "Chapter")["content"] == "recover this unsaved thought"
    storage.save_note("Book", "Chapter", "external change while app was closed")
    restarted = WorkspaceContext(App(storage.paths.root))
    buffer = restarted.notes.open("Book", "Chapter")
    assert buffer.recovered
    assert buffer.document.toPlainText() == "recover this unsaved thought"
    with pytest.raises(ValueError, match="changed on disk"):
        buffer.save()
    assert storage.load_note("Book", "Chapter") == "external change while app was closed"


def test_recovery_survives_deleted_note_file(notes):
    notes.editor.setPlainText("draft")
    notes._buffer.flush_recovery()
    notes.app.storage._note_path("Book", "Chapter").unlink()
    restarted = WorkspaceContext(App(notes.app.paths.root))
    buffer = restarted.notes.open("Book", "Chapter")
    assert buffer.document.toPlainText() == "draft"
    buffer.save()
    assert restarted.app.storage.load_note("Book", "Chapter") == "draft"
    assert not restarted.app.storage.note_drafts("Book")


def test_crash_after_note_save_before_sidecar_removal_does_not_restore_stale_baseline(notes):
    storage = notes.app.storage
    storage.save_note_draft("Book", "Chapter", "older baseline", "already saved")
    storage.save_note("Book", "Chapter", "already saved")
    restarted = WorkspaceContext(App(notes.app.paths.root))
    buffer = restarted.notes.open("Book", "Chapter")
    assert not buffer.recovered
    assert not buffer.dirty
    assert not storage.note_drafts("Book")
    buffer.document.setPlainText("next edit")
    buffer.save()
    assert storage.load_note("Book", "Chapter") == "next edit"


def test_deleted_note_timer_cannot_resurrect_draft(notes, monkeypatch):
    notes.editor.setPlainText("discard this")
    buffer = notes._buffer
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    notes._delete()
    buffer.flush_recovery()
    assert not notes.app.storage.note_drafts("Book")
    assert not notes.app.storage.note_names("Book")


def test_recovery_write_failure_is_visible_and_text_is_retained(notes, monkeypatch):
    def fail(*args):
        raise OSError("Disk full")
    monkeypatch.setattr(notes.app.storage, "save_note_draft", fail)
    notes.editor.setPlainText("keep me")
    notes._buffer.flush_recovery()
    assert notes.editor.toPlainText() == "keep me"
    assert "failed" in notes.dirty.text()
    assert "Disk full" in notes.dirty.toolTip()


def test_explicit_discard_clears_recovery_copy_on_close(window, monkeypatch):
    window.workspace.set_focused_component("notes")
    component = window.workspace.focused.component
    select(component, "One")
    component.editor.setPlainText("discard")
    component._buffer.flush_recovery()
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Discard)
    assert window.close()
    restarted = WorkspaceContext(App(window.app.paths.root))
    assert not restarted.notes.dirty_buffers()
    assert restarted.app.storage.load_note("Book", "One") == "original"


def test_capture_in_original_module_preserves_markdown_draft(window):
    window.workspace.set_focused_component("notes")
    note = window.workspace.focused.component
    select(note, "One")
    note.editor.setPlainText("unfinished note")
    dialog = CaptureDialog(window.ctx, "Book", "source text", {"kind": "pdf", "filename": "book.pdf", "page": 1})
    dialog.prompt.setText("Why does this work?")
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    dialog.save()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert len(LearningStore(window.app.storage).load("Book")) == 1
    assert not LearningStore(window.app.storage).load("Other")
    assert window.ctx.notes.open("Book", "One").document.toPlainText() == "unfinished note"
    dialog.deleteLater()


def test_review_hides_source_until_attempt_and_never_grades_automatically(window):
    store = LearningStore(window.app.storage)
    item = capture(store)
    review = ReviewDialog(window.ctx, "Book", item)
    assert review.reference.isHidden()
    assert not review.reference.toPlainText()
    assert not review.reveal.isEnabled()
    assert not review.good.isEnabled()
    review.rate("good")
    assert store.load("Book")[item["id"]]["card"]["reps"] == 0
    review.answer.setPlainText("Here is my explanation.")
    review.show_source()
    assert item["quote"] in review.reference.toPlainText()
    assert review.answer.isReadOnly()
    assert store.load("Book")[item["id"]]["card"]["reps"] == 0
    review.rate("good")
    assert review.result() == QDialog.DialogCode.Accepted
    assert store.load("Book")[item["id"]]["card"]["reps"] == 1
    review.rate("good")  # stale second delivery must not count twice
    assert store.load("Book")[item["id"]]["card"]["reps"] == 1
    review.deleteLater()


def test_failed_capture_save_keeps_dialog_text(window, monkeypatch):
    dialog = CaptureDialog(window.ctx, "Book", "quote")
    dialog.prompt.setText("My question?")
    def fail(*a, **kw):
        raise OSError("Disk full")
    monkeypatch.setattr(LearningStore, "save_capture", fail)
    dialog.save()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.prompt.text() == "My question?"
    assert dialog.quote.toPlainText() == "quote"
    assert "Disk full" in dialog.error.text()
    dialog.deleteLater()


def test_cancel_review_preserves_attempt_and_leaves_schedule_unchanged(window, monkeypatch):
    store = LearningStore(window.app.storage)
    item = capture(store)
    dialog = ReviewDialog(window.ctx, "Book", item)
    dialog.show()
    dialog.answer.setPlainText("unfinished explanation")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Cancel)
    dialog.reject()
    assert dialog.isVisible()
    assert dialog.answer.toPlainText() == "unfinished explanation"
    assert store.load("Book")[item["id"]] == item
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Discard)
    dialog.reject()
    assert not dialog.isVisible()
    assert store.load("Book")[item["id"]] == item
    dialog.deleteLater()


def test_learning_refresh_and_resolution(window):
    store = LearningStore(window.app.storage)
    item = capture(store, kind="question")
    component = LearningComponent(window.ctx)
    assert "1 open questions" in component.summary.text()
    assert not component.review_button.isEnabled()
    capture(store, item_id=item["id"], revision=item["revision"], kind="question", resolved=True)
    window.ctx.learning_changed.emit()
    assert "0 open questions" in component.summary.text()
    component.deleteLater()


def make_pdf(window):
    path = window.app.paths.documents_dir("Book") / "book.pdf"
    writer = QPdfWriter(str(path))
    # Use point coordinates; at the default 1200 DPI, Windows' font ascent
    # can put the entire first line above the printable page at y=100.
    writer.setResolution(72)
    painter = QPainter(writer)
    painter.drawText(100, 100, "first page")
    writer.newPage()
    painter.drawText(100, 100, "second page")
    painter.end()
    del writer
    return path


def test_pdf_capture_uses_viewer_module_and_page_after_module_switch(window):
    path = make_pdf(window)
    viewer = PdfViewer(str(path), window.ctx)
    viewer.go_to({"page": 1})
    captures = []
    window.ctx.capture_requested.disconnect(window._capture)
    window.ctx.capture_requested.connect(lambda *args: captures.append(args))
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    viewer._capture()
    assert captures[0][0] == "Book"
    assert "second page" in captures[0][1]
    assert captures[0][2] == {"kind": "pdf", "filename": "book.pdf", "page": 1}
    viewer.deleteLater()


def test_source_link_returns_to_captured_pdf_page(window):
    make_pdf(window)
    window._open_source("Book", {"filename": "book.pdf", "kind": "pdf", "page": 1})
    assert isinstance(window.workspace.focused.component, PdfViewer)
    assert window.workspace.focused.component._view.pageNavigator().currentPage() == 1


def test_workspace_restores_split_notes_preview_and_zoom(window, qt):
    window.workspace.set_focused_component("notes")
    note = window.workspace.focused.component
    select(note, "One")
    note.toggle_preview()
    window.workspace.split_horizontal()
    window.workspace.set_focused_component("learning")
    window.workspace.toggle_zoom()
    window.sidebar.hide()
    assert window.close()
    reopened = MainWindow(window.app.paths.root)
    try:
        assert reopened.app.current_module == "Book"
        assert len(reopened.workspace._panes) == 2
        assert reopened.workspace.is_zoomed
        assert reopened.sidebar.isHidden()
        assert reopened.workspace.focused.factory_key == "learning"
        restored_note = reopened.workspace._panes[0].component
        assert restored_note._current == "One"
        assert restored_note.markdown._preview_panel.isHidden()
        reopened.workspace.toggle_zoom()
        assert reopened.workspace._panes[0].parentWidget().orientation() == Qt.Vertical
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()


def test_workspace_restores_reader_from_its_original_module(window, qt):
    path = make_pdf(window)
    viewer = window._open_viewer(str(path))
    viewer.go_to({"page": 1})
    window.app.cmd_create("Other")
    window.app.cmd_use("Other")
    window.ctx.module_changed.emit("Other")
    window.close()
    reopened = MainWindow(window.app.paths.root)
    try:
        assert reopened.app.current_module == "Other"
        viewer = reopened.workspace.focused.component
        assert isinstance(viewer, PdfViewer)
        assert viewer._module == "Book"
        assert viewer._view.pageNavigator().currentPage() == 1
    finally:
        reopened.close()
        reopened.deleteLater()
        qt.processEvents()
