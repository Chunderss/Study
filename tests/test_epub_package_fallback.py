"""A book whose container.xml or package file is damaged still opens, in a
fixed order, and the reader says why it reads differently."""
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from xml.etree import ElementTree

import pytest
pytest.importorskip("PySide6")
from vocab.gui.viewers import EpubBook

CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
             '<rootfile full-path="{}" media-type="application/oebps-package+xml"/>'
             '</rootfiles></container>')
PACKAGE = ('<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" '
           'xmlns:dc="http://purl.org/dc/elements/1.1/"><metadata><dc:title>{title}</dc:title>'
           '</metadata><manifest><item id="one" href="one.xhtml"/><item id="two" href="two.xhtml"/>'
           '</manifest><spine><itemref idref="two"/><itemref idref="one"/></spine></package>')
PAGE = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title></head><body><p>{0}</p></body></html>'


def make_book(path, entries):
    with zipfile.ZipFile(path, "w") as book:
        for name, data in entries.items():
            book.writestr(name, data)
    return str(path)


def chapters(book):
    return [(label, Path(file).relative_to(book.tmpdir).as_posix()) for label, file in book.chapters]


def test_a_damaged_container_falls_back_to_searching_for_the_package(tmp_path):
    pages = {"OEBPS/one.xhtml": PAGE.format("One"), "OEBPS/two.xhtml": PAGE.format("Two"),
             "OEBPS/content.opf": PACKAGE.format(title="The Real Title")}
    spine = [("two", "OEBPS/two.xhtml"), ("one", "OEBPS/one.xhtml")]

    damaged = make_book(tmp_path / "damaged.epub", dict(
        pages, **{"META-INF/container.xml": CONTAINER.format("OEBPS/content.opf&secret-name")}))
    book = EpubBook(damaged)
    assert chapters(book) == spine and book.title == "The Real Title"
    assert len(book.warnings) == 1
    assert book.warnings[0].startswith("This book's container.xml could not be read "
                                       "(markup error at line 1, column ")
    assert "secret" not in book.warnings[0] and "content" not in book.warnings[0]

    missing = make_book(tmp_path / "missing.epub", dict(
        pages, **{"META-INF/container.xml": CONTAINER.format("OEBPS/missing.opf")}))
    book = EpubBook(missing)
    assert chapters(book) == spine and book.title == "The Real Title"
    assert book.warnings == ["This book's container.xml names a package file the book does not "
                             "contain, so its package file was found by searching the book."]

    # A path leaving the book still refuses it.
    outside = make_book(tmp_path / "outside.epub", dict(
        pages, **{"META-INF/container.xml": CONTAINER.format("../../outside.opf")}))
    with pytest.raises(ValueError, match="outside"):
        EpubBook(outside)

    # A sound book reads as before, without a warning.
    sound = make_book(tmp_path / "sound.epub", dict(
        pages, **{"META-INF/container.xml": CONTAINER.format("OEBPS/content.opf")}))
    book = EpubBook(sound)
    assert chapters(book) == spine and book.warnings == []


PACKAGES = {
    "ampersand": (PACKAGE.format(title="Pride & Prejudice"), "markup error at line 1, column "),
    "mismatched-tag": (PACKAGE.replace("</manifest>", "</spine>"), "markup error at line 1, column "),
    "unknown-encoding": (PACKAGE.replace("utf-8", "x-unknown-charset"), "an unsupported character encoding"),
    "multibyte-encoding": (PACKAGE.replace("utf-8", "shift_jis"), "an unsupported character encoding"),
}


@pytest.mark.parametrize("damage", PACKAGES)
def test_an_unreadable_package_lists_chapters_in_file_name_order(tmp_path, monkeypatch, damage):
    package, problem = PACKAGES[damage]
    # Written out of order; folders differ by more than case, so the order
    # holds on case-insensitive file systems too. Code points would put C first.
    entries = {"META-INF/container.xml": CONTAINER.format("content.opf"), "content.opf": package,
               "b/2.html": PAGE.format("2"), "C/3.html": PAGE.format("3"), "a/1.html": PAGE.format("1")}
    path = make_book(tmp_path / "book.epub", entries)
    book = EpubBook(path)
    assert chapters(book) == [("1.html", "a/1.html"), ("2.html", "b/2.html"), ("3.html", "C/3.html")]
    assert book.title == "book"
    assert len(book.warnings) == 1
    assert book.warnings[0].startswith(f"This book's package file could not be read ({problem}")
    assert book.warnings[0].endswith("Chapters are listed in file-name order, which may differ "
                                     "from the book's reading order.")

    if damage == "ampersand":
        # Reading a file at all is not a parse error: the book is refused.
        parse = ElementTree.parse
        for refused in ("content.opf", "container.xml"):
            def unreadable(source, parser=None, refused=refused):
                if str(source).endswith(refused):
                    raise PermissionError(13, "Permission denied")
                return parse(source, parser)
            monkeypatch.setattr(ElementTree, "parse", unreadable)
            with pytest.raises(PermissionError):
                EpubBook(path)


def test_the_reader_shows_why_a_damaged_book_reads_differently(tmp_path):
    script = r'''
import sys
import zipfile
from pathlib import Path
from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import EpubViewer

app = QApplication([])
window = MainWindow(Path(sys.argv[1]))
try:
    window.app.cmd_create("Book")
    window.app.cmd_use("Book")
    window.ctx.module_changed.emit("Book")
    path = window.app.paths.documents_dir("Book") / "damaged.epub"
    page = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title></head><body><p>{0}</p></body></html>'
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("content.opf", '<package xmlns:dc="http://purl.org/dc/elements/1.1/">'
                      '<dc:title>Pride & Prejudice</dc:title></package>')
        book.writestr("Text/b.xhtml", page.format("B"))
        book.writestr("Text/a.xhtml", page.format("A"))
    window.show()
    viewer = window._open_viewer(str(path))
    assert isinstance(viewer, EpubViewer), window.ctx.messages
    warning = ("This book's package file could not be read (markup error at line 1, column 70). "
               "Chapters are listed in file-name order, which may differ from the book's reading order.")
    QTest.qWait(50)  # the pane shows its new reader after this event
    assert viewer._notice.isVisible() and viewer._notice.text() == warning, viewer._notice.text()
    assert f"! damaged.epub: {warning}" in window.ctx.messages, window.ctx.messages
    assert window.workspace.focused.title.text() == "EPUB · damaged"
    assert [viewer._picker.itemText(i) for i in range(viewer._picker.count())] == ["1. a.xhtml", "2. b.xhtml"]
    for _ in range(500):
        if viewer._document_loaded and viewer._web.title() == "A":
            break
        QTest.qWait(20)
    viewer._go(1)
    for _ in range(500):
        if viewer._document_loaded and viewer._web.title() == "B":
            break
        QTest.qWait(20)
    assert viewer._web.title() == "B" and viewer._notice.isVisible()
finally:
    window.close()
    window.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path)],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def spine_book(path, hrefs, ids=None):
    """A book whose reading order lists ``hrefs``, of which only the files
    named one, two and three.xhtml exist."""
    ids = ids or [f"i{n}" for n in range(len(hrefs))]
    manifest = "".join(f'<item id="i{n}" href="{href}"/>' for n, href in enumerate(hrefs))
    spine = "".join(f'<itemref idref="{idref}"/>' for idref in ids)
    return make_book(path, {
        "META-INF/container.xml": CONTAINER.format("content.opf"),
        "content.opf": f'<package xmlns="http://www.idpf.org/2007/opf"><manifest>{manifest}</manifest>'
                       f'<spine>{spine}</spine></package>',
        "one.xhtml": PAGE.format("One"), "two.xhtml": PAGE.format("Two"),
        "three.xhtml": PAGE.format("Three")})


def test_reading_order_pages_given_by_web_address_are_skipped(tmp_path):
    local = [("one", "one.xhtml"), ("two", "two.xhtml"), ("three", "three.xhtml")]
    book = EpubBook(spine_book(tmp_path / "web.epub", [
        "one.xhtml", "https://example.com/x.xhtml", "two.xhtml", "http://example.com/y.xhtml",
        "data:text/html,hello", "three.xhtml"]))
    assert chapters(book) == local
    assert book.warnings == ["Skipped 3 pages of this book's reading order given as web addresses; "
                             "the reader shows only pages inside the book."]
    book = EpubBook(spine_book(tmp_path / "one-web.epub", [
        "one.xhtml", "//example.com/x.xhtml", "two.xhtml", "three.xhtml"]))
    assert chapters(book) == local
    assert book.warnings == ["Skipped 1 page of this book's reading order given as a web address; "
                             "the reader shows only pages inside the book."]

    # Pages the book does not have, or no page at all, were already skipped.
    book = EpubBook(spine_book(tmp_path / "missing.epub", [
        "one.xhtml", "gone.xhtml", "two.xhtml", "three.xhtml"], ids=["i0", "i1", "i2", "nothing", "i3"]))
    assert chapters(book) == local
    assert book.warnings == ["Skipped 2 pages of this book's reading order missing from the book."]

    # Local paths leaving the book still refuse it, file: addresses included.
    for escaping in ("../../x.xhtml", "C:/x.xhtml", "file:///etc/passwd", "file://server/share/x.xhtml"):
        with pytest.raises(ValueError, match="inside the book|outside the book"):
            EpubBook(spine_book(tmp_path / "escaping.epub", ["one.xhtml", escaping]))
