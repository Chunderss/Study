"""Verify in-app document viewers: EPUB stdlib parse + viewer instantiation +
that Documents 'open' routes pdf/epub to view_requested (in-app) not the OS."""
import os
import sys
import tempfile
import zipfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from vocab.gui import theme


def make_epub(path):
    """Write a tiny but valid EPUB (container.xml + OPF + 2 spine chapters)."""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?>'
                   '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?>'
                   '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">'
                   '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   '<dc:title>Test Book</dc:title></metadata>'
                   '<manifest>'
                   '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest>'
                   '<spine><itemref idref="c1"/><itemref idref="c2"/></spine>'
                   '</package>')
        z.writestr("OEBPS/ch1.xhtml", "<html><body><h1>Chapter One</h1><p>Hello.</p></body></html>")
        z.writestr("OEBPS/ch2.xhtml", "<html><body><h1>Chapter Two</h1><p>World.</p></body></html>")


def make_pdf(path):
    # minimal valid-ish PDF header (QtPdf will report a page count of >=0)
    with open(path, "wb") as f:
        f.write(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
                b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
                b"trailer<</Root 1 0 R>>\n%%EOF")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    tmp = tempfile.mkdtemp(prefix="vocab_view_")

    # --- EPUB parse (stdlib, no Qt) ---
    from vocab.gui.viewers import EpubBook, EpubViewer, make_viewer
    epath = os.path.join(tmp, "test.epub")
    make_epub(epath)
    book = EpubBook(epath)
    assert book.title == "Test Book", f"title parse failed: {book.title!r}"
    assert len(book.chapters) == 2, f"expected 2 chapters, got {len(book.chapters)}"
    assert all(os.path.exists(p) for _n, p in book.chapters), "chapter files missing"
    print(f"[ok] EPUB parsed: '{book.title}' with {len(book.chapters)} chapters (stdlib)")

    # --- viewer factory returns the right types ---
    ev = make_viewer(epath)
    assert ev.__class__.__name__ == "EpubViewer", "epub -> EpubViewer"
    assert hasattr(ev, "TITLE") and hasattr(ev, "focus_default"), "viewer missing pane protocol"
    print(f"[ok] EpubViewer built: TITLE={ev.TITLE!r}, chapters in picker={ev._picker.count()}")

    ppath = os.path.join(tmp, "test.pdf")
    make_pdf(ppath)
    pv = make_viewer(ppath)
    assert pv.__class__.__name__ == "PdfViewer", "pdf -> PdfViewer"
    print(f"[ok] PdfViewer built: TITLE={pv.TITLE!r}")

    assert make_viewer(os.path.join(tmp, "notes.txt")) is None, "txt should have no in-app viewer"
    print("[ok] unsupported type -> no viewer (falls back to OS)")

    # --- Documents 'open' routes pdf/epub to view_requested (in-app), not OS ---
    from vocab.gui.main_window import MainWindow
    win = MainWindow(root=tempfile.mkdtemp(prefix="vocab_view_home_"))
    win.app.cmd_create("Book"); win.app.cmd_use("Book")
    win.app.storage.add_document("Book", epath)
    win._refresh_modules(); win.ctx.module_changed.emit("Book")

    captured = {}
    win.ctx.view_requested.connect(lambda p: captured.setdefault("path", p))
    # find the Documents component (make one bound to ctx) and open the epub
    from vocab.gui.components import DocumentsComponent
    dc = DocumentsComponent(win.ctx)
    dc.refresh()
    # select the epub row and open
    for i in range(dc.docs.count()):
        if dc.docs.item(i).text().endswith(".epub"):
            dc.docs.setCurrentRow(i); break
    n_before = len(win.workspace._panes)
    dc._open_selected()
    app.processEvents()
    assert "path" in captured and captured["path"].endswith(".epub"), \
        "opening an epub did not emit view_requested (in-app)"
    print("[ok] Documents.open(epub) -> view_requested (in-app, not Calibre)")

    # and the MainWindow handler REPLACES the focused pane with a viewer (not split)
    n_panes = len(win.workspace._panes)
    win.workspace.set_focused_component("documents")   # make the focused pane Documents
    win.ctx.view_requested.emit(epath)
    app.processEvents()
    assert len(win.workspace._panes) == n_panes, "viewer should REPLACE the pane, not split a new one"
    focused = win.workspace.focused
    assert focused.component.__class__.__name__ == "EpubViewer", \
        f"focused pane not the viewer; is {focused.component.__class__.__name__}"
    print("[ok] opening a document REPLACES the Documents pane (no new pane)")

    # back_requested restores the Documents component in the same pane
    focused.component.back_requested.emit()
    app.processEvents()
    assert win.workspace.focused.component.__class__.__name__ == "DocumentsComponent", \
        "‹ Documents did not restore the Documents component"
    print("[ok] ‹ Documents restores the file list in the same pane")

    # font-size zoom on the EPUB viewer
    ev2 = make_viewer(epath)
    z0 = ev2._zoom
    ev2._bump(EpubViewer.ZOOM_STEP)
    assert ev2._zoom > z0, "A+ did not increase EPUB zoom"
    ev2._bump(1 / EpubViewer.ZOOM_STEP)
    assert abs(ev2._zoom - z0) < 1e-6, "A- did not return to prior zoom"
    print(f"[ok] EPUB font-size zoom works (default {z0}, clamped {EpubViewer.ZOOM_MIN}-{EpubViewer.ZOOM_MAX})")

    # --- reading position persistence -----------------------------------
    st = win.app.storage
    # storage round-trip
    st.save_reading_pos("Book", "test.epub", {"chapter": 1, "scroll": 0.5, "zoom": 1.6})
    got = st.load_reading_pos("Book", "test.epub")
    assert got == {"chapter": 1, "scroll": 0.5, "zoom": 1.6}, f"reading pos round-trip failed: {got}"
    print("[ok] storage.save/load_reading_pos round-trips")

    # a NEW EpubViewer for that book must restore chapter + zoom from storage
    win.app.storage.add_document("Book", epath)   # ensure present (test.epub name)
    # point the viewer's ctx at module 'Book'
    win.app.cmd_use("Book")
    ev3 = make_viewer(os.path.join(tmp, "test.epub"), win.ctx)
    assert ev3._idx == 1, f"viewer did not restore saved chapter (idx={ev3._idx})"
    assert abs(ev3._zoom - 1.6) < 1e-6, f"viewer did not restore saved zoom ({ev3._zoom})"
    print(f"[ok] new EpubViewer restored saved position: chapter={ev3._idx}, zoom={ev3._zoom}")

    # on_replaced (pane swap) persists the current chapter
    ev3._load(0)
    ev3.on_replaced()
    got2 = st.load_reading_pos("Book", "test.epub")
    assert got2.get("chapter") == 0, f"on_replaced did not persist chapter: {got2}"
    print("[ok] on_replaced (pane swap / back button) saves current chapter")

    print("\nVIEWERS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
