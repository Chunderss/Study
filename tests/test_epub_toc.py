"""EPUB chapters are labelled from the book's table of contents, in the
book's reading order."""
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest
pytest.importorskip("PySide6")
from vocab.gui.viewers import EpubBook

CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
             '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
             '</rootfiles></container>')
PAGE = '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{0}</title></head><body><p>{0}</p></body></html>'
DOCTYPE = ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
           '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">')
NAV = (DOCTYPE + '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
       '<head><title>Contents</title></head><body>{}</body></html>')


def ncx(points):
    return ('<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
            '<navMap>' + "".join(
                f'<navPoint id="p{n}"><navLabel><text>{label}</text></navLabel><content src="{src}"/></navPoint>'
                for n, (label, src) in enumerate(points)) + '</navMap></ncx>')


def make_book(path, spine, manifest_extra="", files=None, toc_attribute=""):
    """A book with pages Text/<name>.xhtml read in ``spine`` order."""
    names = sorted(set(spine))
    manifest = "".join(f'<item id="{name}" href="Text/{name}.xhtml" media-type="application/xhtml+xml"/>'
                       for name in names)
    package = ('<package xmlns="http://www.idpf.org/2007/opf" version="3.0"><manifest>'
               + manifest + manifest_extra + f'</manifest><spine{toc_attribute}>'
               + "".join(f'<itemref idref="{name}"/>' for name in spine) + '</spine></package>')
    with zipfile.ZipFile(path, "w") as book:
        book.writestr("META-INF/container.xml", CONTAINER)
        book.writestr("OEBPS/content.opf", package)
        for name in names:
            book.writestr(f"OEBPS/Text/{name}.xhtml", PAGE.format(name))
        for name, data in (files or {}).items():
            book.writestr("OEBPS/" + name, data)
    return str(path)


def labels(book):
    return [label for label, _path in book.chapters]


def files(book):
    return [Path(path).name for _label, path in book.chapters]


NAV_ITEM = '<item id="nav" href="nav/toc.xhtml" properties="nav" media-type="application/xhtml+xml"/>'
NCX_ITEM = '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'


def test_chapter_titles_come_from_the_epub3_navigation_in_spine_order(tmp_path):
    nav = NAV.format(
        '<nav epub:type="landmarks"><ol><li><a href="../Text/c2.xhtml">Start of content</a></li></ol></nav>'
        '<nav epub:type="toc"><h1>Contents</h1><ol>'
        '<li><a href="../Text/cover.xhtml">Cover</a></li>'
        '<li><a href="../Text/c1.xhtml">Part   I:\n <span>Café 日本</span></a>'
        '<ol><li><a href="../Text/c1.xhtml#s1">Chapter 1</a></li></ol></li>'
        '<li><a href="../Text/notes%20one.xhtml">Notes</a></li>'
        '<li><a href="https://example.com/x.xhtml">Elsewhere</a></li>'
        '<li><a href="../../../escape.xhtml">Escaping</a></li>'
        '<li><a href="../Text/c2.xhtml#top">Chapter&nbsp;2 &mdash; The End</a></li>'
        '</ol></nav>'
        '<nav epub:type="page-list"><ol><li><a href="../Text/c1_b.xhtml">7</a></li></ol></nav>')
    path = make_book(tmp_path / "book.epub", ["cover", "c1", "c1_b", "c2", "c1"],
                     manifest_extra=NAV_ITEM + '<item id="notes" href="Text/notes%20one.xhtml"/>',
                     files={"nav/toc.xhtml": nav, "Text/notes one.xhtml": PAGE.format("Notes")})
    book = EpubBook(path)
    # Reading order is the spine's: nothing reordered, merged, added or dropped.
    assert files(book) == ["cover.xhtml", "c1.xhtml", "c1_b.xhtml", "c2.xhtml", "c1.xhtml"]
    assert labels(book) == ["Cover", "Part I: Café 日本", "c1 b", "Chapter 2 — The End",
                            "Part I: Café 日本"]
    assert book.warnings == []


def test_ncx_titles_fill_in_when_navigation_is_missing_or_damaged(tmp_path):
    spine = ["c1", "c2", "c3"]
    table = ncx([("Second", "Text/c2.xhtml"), ("First", "Text/c1.xhtml#start"),
                 ("First again", "Text/c1.xhtml")])

    # EPUB 2: only an NCX, which lists c2 before c1.
    book = EpubBook(make_book(tmp_path / "ncx.epub", spine, NCX_ITEM, {"toc.ncx": table},
                              toc_attribute=' toc="ncx"'))
    assert files(book) == ["c1.xhtml", "c2.xhtml", "c3.xhtml"]
    assert labels(book) == ["First", "Second", "c3"]

    # A damaged navigation document: the NCX labels.
    damaged = NAV.format('<nav epub:type="toc"><ol><li><a href="../Text/c1.xhtml">Nav one</a></li></ol>')
    book = EpubBook(make_book(tmp_path / "damaged.epub", spine, NAV_ITEM + NCX_ITEM,
                              {"nav/toc.xhtml": damaged, "toc.ncx": table}))
    assert labels(book) == ["First", "Second", "c3"]

    # Labels are chosen per file: the navigation document first, then the NCX.
    partial = NAV.format('<nav epub:type="toc"><ol><li><a href="../Text/c1.xhtml">Nav one</a></li></ol></nav>')
    book = EpubBook(make_book(tmp_path / "partial.epub", spine, NAV_ITEM + NCX_ITEM,
                              {"nav/toc.xhtml": partial, "toc.ncx": table}))
    assert labels(book) == ["Nav one", "Second", "c3"]

    # Both damaged, HTML entities without a DTD, or no table of contents
    # among the navigation's lists: file names.
    no_doctype = ('<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
                  '<body><nav epub:type="toc"><a href="../Text/c1.xhtml">One&nbsp;</a></nav></body></html>')
    landmarks = NAV.format('<nav epub:type="landmarks"><a href="../Text/c1.xhtml">Begin</a></nav>')
    for name, nav, table_file in (("both", damaged, table.replace("</navMap>", "")),
                                  ("entities", no_doctype, None), ("landmarks", landmarks, None)):
        extra = {"nav/toc.xhtml": nav}
        if table_file is not None:
            extra["toc.ncx"] = table_file
        book = EpubBook(make_book(tmp_path / f"{name}.epub", spine,
                                  NAV_ITEM + (NCX_ITEM if table_file is not None else ""), extra))
        assert labels(book) == ["c1", "c2", "c3"], name
        assert book.warnings == [], name


def test_picker_shows_toc_titles_and_shortens_long_ones(tmp_path):
    long_title = "A very long chapter title " * 8
    long_title = long_title.strip()
    assert len(long_title) > 200
    nav = NAV.format(
        '<nav epub:type="toc"><ol>'
        f'<li><a href="../Text/c1.xhtml">{long_title}</a></li>'
        '<li><a href="../Text/c2.xhtml">&lt;b&gt;Bold&lt;/b&gt; &lt;img src="x.png"&gt;</a></li>'
        f'<li><a href="../Text/c3.xhtml">&lt;img src="x.png"&gt;{"x" * 90}</a></li>'
        '</ol></nav>')
    path = make_book(tmp_path / "book.epub", ["c1", "c2", "c3"], NAV_ITEM, {"nav/toc.xhtml": nav})
    script = r'''
import html
import sys
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from vocab.gui.viewers import EpubViewer

app = QApplication([])
viewer = EpubViewer(sys.argv[1])
try:
    viewer.resize(500, 400)
    viewer.show()
    picker = viewer._picker
    long_title = sys.argv[2]
    texts = [picker.itemText(i) for i in range(picker.count())]
    marked = '<img src="x.png">' + "x" * 90
    assert texts == ["1. " + long_title[:79] + "…", '2. <b>Bold</b> <img src="x.png">',
                     "3. " + marked[:79] + "…"], texts
    assert picker.itemData(0, Qt.ToolTipRole) == "<p>" + long_title + "</p>"
    assert picker.itemData(1, Qt.ToolTipRole) is None, repr(picker.itemData(1, Qt.ToolTipRole))
    assert picker.itemData(2, Qt.ToolTipRole) == "<p>" + html.escape(marked) + "</p>"
    # The shortened titles leave the reader as narrow as before.
    assert picker.minimumSizeHint().width() < 200, (picker.minimumSizeHint(), picker.sizeHint())
    for _ in range(500):
        if viewer._document_loaded and viewer._web.title() == "c1":
            break
        QTest.qWait(20)
    viewer._go(1)
    for _ in range(500):
        if viewer._document_loaded and viewer._web.title() == "c2":
            break
        QTest.qWait(20)
    assert viewer._web.title() == "c2" and picker.currentIndex() == 1
finally:
    viewer.close()
    viewer.deleteLater()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, path, long_title],
                            cwd=Path(__file__).resolve().parents[1], env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
