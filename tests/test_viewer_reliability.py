import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import zipfile
import pytest
pytest.importorskip("PySide6")
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainter, QPdfWriter
from .test_markdown_preview import qt
from vocab.cli.app import App
from vocab.gui.context import WorkspaceContext
from vocab.gui.viewers import EpubBook, PdfViewer


def epub(path, opf_path="OPS/book.opf", href="chapter.xhtml"):
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("META-INF/container.xml", f'''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="{opf_path}"/></rootfiles></container>''')
        book.writestr("OPS/book.opf", f'''<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="chapter" href="{href}"/></manifest><spine><itemref idref="chapter"/></spine></package>''')
        book.writestr("OPS/chapter.xhtml", "<html><body>chapter text</body></html>")


def test_epub_parses_chapter_with_fragment(tmp_path):
    path = tmp_path / "book.epub"
    epub(path, href="chapter.xhtml#start")
    book = EpubBook(str(path))
    assert len(book.chapters) == 1
    assert Path(book.chapters[0][1]).read_text().startswith("<html>")
    book._temp.cleanup()


@pytest.mark.parametrize("kwargs", [{"opf_path": "../../outside.opf"}, {"href": "../../outside.html"}])
def test_epub_rejects_paths_outside_book(tmp_path, kwargs):
    path = tmp_path / "book.epub"
    epub(path, **kwargs)
    with pytest.raises(ValueError, match="outside"):
        EpubBook(str(path))


def test_pdf_restores_position_even_if_load_finished_synchronously(qt, tmp_path):
    app = App(tmp_path / "data")
    app.cmd_create("Book")
    app.cmd_use("Book")
    pdf = app.paths.documents_dir("Book") / "book.pdf"
    writer = QPdfWriter(str(pdf))
    painter = QPainter(writer)
    for i in range(3):
        if i: writer.newPage()
        painter.drawText(100, 100, str(i))
    painter.end()
    del writer
    app.storage.save_reading_pos("Book", "book.pdf", {"page": 2, "zoom": 1.2})
    ctx = WorkspaceContext(app)
    view = PdfViewer(str(pdf), ctx)
    assert view._view.pageNavigator().currentPage() == 2
    assert view._view.zoomFactor() == pytest.approx(1.2)
    view.deleteLater()
    qt.processEvents()


def test_invalid_pdf_reports_error(qt, tmp_path):
    path = tmp_path / "bad.pdf"
    path.write_text("not a pdf")
    with pytest.raises(ValueError, match="Could not read PDF"):
        PdfViewer(str(path))
