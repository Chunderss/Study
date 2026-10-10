"""An EPUB is an untrusted archive: the reader refuses one that would extract
past its size or file limits, before writing, and says why."""
import io
import os
import subprocess
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

import pytest
pytest.importorskip("PySide6")
from vocab.gui.viewers import EpubBook

ROOT = Path(__file__).resolve().parents[1]

# Small limits for these tests, and the files every test book holds.
ENTRIES, ENTRY_BYTES, TOTAL_BYTES = 6, 10 << 10, 20 << 10
CONTAINER = '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>'
PACKAGE = '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c" href="chapter.xhtml"/></manifest><spine><itemref idref="c"/></spine></package>'
CHAPTER = "<html><body>chapter text</body></html>"
FIXED = len(CONTAINER) + len(PACKAGE) + len(CHAPTER)  # bytes in those three files


def book_with(path, files):
    """A one-chapter book plus zero-filled files: (name, size) pairs."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # repeated names are written on purpose
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
            book.writestr("META-INF/container.xml", CONTAINER)
            book.writestr("OPS/book.opf", PACKAGE)
            book.writestr("OPS/chapter.xhtml", CHAPTER)
            for name, size in files:
                book.writestr(name, b"\0" * size)


@pytest.fixture
def book_temp(tmp_path, monkeypatch):
    """Extract books into this test's own folder, with small limits."""
    folder = tmp_path / "temp"
    folder.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    monkeypatch.setattr(EpubBook, "MAX_ENTRIES", ENTRIES, raising=False)
    monkeypatch.setattr(EpubBook, "MAX_ENTRY_BYTES", ENTRY_BYTES, raising=False)
    monkeypatch.setattr(EpubBook, "MAX_TOTAL_BYTES", TOTAL_BYTES, raising=False)
    return folder


def recorded_reads(monkeypatch):
    """Names of the archive entries opened from now on, and the bytes read."""
    record, real_open = {"names": [], "bytes": 0}, zipfile.ZipFile.open

    def recording_open(self, name, mode="r", *args, **kwargs):
        stream = real_open(self, name, mode, *args, **kwargs)
        if mode != "r":
            return stream
        record["names"].append(getattr(name, "filename", name))
        real_read = stream.read

        def read(size=-1):
            data = real_read(size)
            record["bytes"] += len(data)
            return data
        stream.read = read
        return stream
    monkeypatch.setattr(zipfile.ZipFile, "open", recording_open)
    return record


@pytest.mark.parametrize("kind, fits, over, message", [
    ("files", [(f"OPS/{i}.css", 0) for i in range(3)], [(f"OPS/{i}.css", 0) for i in range(4)],
     r"contains 7 files; the reader opens books with up to 6\."),
    ("per file", [("OPS/big.png", ENTRY_BYTES)], [("OPS/big.png", ENTRY_BYTES + 1)],
     r"holds a file that would extract to 10\.1 KB, more than the 10\.0 KB the reader allows per file\."),
    ("per book", [("OPS/1.png", ENTRY_BYTES), ("OPS/2.png", TOTAL_BYTES - ENTRY_BYTES - FIXED)],
     [("OPS/1.png", ENTRY_BYTES), ("OPS/2.png", TOTAL_BYTES - ENTRY_BYTES - FIXED + 1)],
     r"would extract to 20\.1 KB, more than the 20\.0 KB the reader allows per book\."),
])
def test_epub_over_a_limit_is_refused_before_anything_is_extracted(
        tmp_path, monkeypatch, book_temp, kind, fits, over, message):
    path = tmp_path / "book.epub"
    book_with(path, fits)
    with zipfile.ZipFile(path) as book:
        sizes = [entry.file_size for entry in book.infolist()]
    assert {"files": len(sizes) == ENTRIES, "per file": max(sizes) == ENTRY_BYTES,
            "per book": sum(sizes) == TOTAL_BYTES}[kind]
    EpubBook(str(path))._temp.cleanup()  # a book exactly at the limit opens
    book_with(path, over)
    reads = recorded_reads(monkeypatch)
    with pytest.raises(ValueError, match=message):
        EpubBook(str(path))
    assert reads["names"] == []  # refused on the declared sizes, before writing
    assert list(book_temp.iterdir()) == []  # and nothing is left behind


def test_epub_with_repeated_names_extracts_each_entry_once(tmp_path, monkeypatch, book_temp):
    # By name, extraction would write the last "x.png" once for each of the
    # three: three times what the book declares.
    path = tmp_path / "book.epub"
    book_with(path, [("OPS/x.png", 0), ("OPS/x.png", 0), ("OPS/x.png", ENTRY_BYTES)])
    reads = recorded_reads(monkeypatch)
    book = EpubBook(str(path))
    try:
        assert reads["bytes"] == FIXED + ENTRY_BYTES
        assert os.path.getsize(os.path.join(book.tmpdir, "OPS", "x.png")) == ENTRY_BYTES  # last wins
    finally:
        book._temp.cleanup()


def test_epub_extraction_stops_at_the_book_limit_however_entries_are_visited(
        tmp_path, monkeypatch, book_temp):
    # The declared sizes pass, but extraction by name (as extractall() does
    # without a member list) would write the last "x.png" three times.
    path = tmp_path / "book.epub"
    book_with(path, [("OPS/x.png", 0), ("OPS/x.png", 0), ("OPS/x.png", ENTRY_BYTES)])
    real_extractall = zipfile.ZipFile.extractall
    monkeypatch.setattr(zipfile.ZipFile, "extractall",
                        lambda self, path=None, members=None, pwd=None: real_extractall(self, path, None, pwd))
    reads = recorded_reads(monkeypatch)
    with pytest.raises(ValueError, match=r"would extract to more than the 20\.0 KB the reader allows per book\."):
        EpubBook(str(path))
    assert reads["bytes"] <= TOTAL_BYTES + 1  # stopped at the first byte too many
    assert list(book_temp.iterdir()) == []


def test_epub_entry_larger_than_declared_is_stopped_while_extracting(
        tmp_path, monkeypatch, book_temp):
    path = tmp_path / "book.epub"
    book_with(path, [("OPS/image.png", 16)])
    # CPython's zipfile already stops at the declared size; this stands in for
    # a reader that would not, which must not get past the limits either.
    class Inflated(io.BytesIO):
        consumed = 0

        def read(self, size=-1):
            data = super().read(size)
            self.consumed += len(data)
            return data
    inflated = Inflated(b"\0" * 1_000_000)
    real_open = zipfile.ZipFile.open

    def lying_open(self, name, mode="r", *args, **kwargs):
        if getattr(name, "filename", name) == "OPS/image.png":
            return inflated
        return real_open(self, name, mode, *args, **kwargs)
    monkeypatch.setattr(zipfile.ZipFile, "open", lying_open)
    with pytest.raises(ValueError, match="holds a file with more data than the book declares"):
        EpubBook(str(path))
    assert inflated.consumed <= 17  # stopped at the first byte too many
    assert list(book_temp.iterdir()) == []


def test_refused_epub_says_why_and_keeps_the_pane(tmp_path):
    script = r'''
import sys
import zipfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from vocab.gui.main_window import MainWindow
from vocab.gui.viewers import EpubBook

app = QApplication([])
EpubBook.MAX_TOTAL_BYTES = 20 << 10
window = MainWindow(Path(sys.argv[1]))
window.app.cmd_create("Book")
window.app.cmd_use("Book")
window.ctx.module_changed.emit("Book")
path = window.app.paths.documents_dir("Book") / "book.epub"
with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c" href="c.html"/></manifest><spine><itemref idref="c"/></spine></package>')
    book.writestr("c.html", "<html><body>text</body></html>")
    book.writestr("big.png", b"\0" * (64 << 10))
window.show()
app.processEvents()
pane = window.workspace.focused
before = pane.component
assert window._open_viewer(str(path)) is None
assert pane.component is before
status = window.status.text()
assert status.startswith("! Could not open document: This EPUB would extract to 64."), status
assert status.endswith("more than the 20.0 KB the reader allows per book."), status
window.close()
'''
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"
    env["QTWEBENGINE_CHROMIUM_FLAGS"] = env.get("QTWEBENGINE_CHROMIUM_FLAGS", "") + " --disable-gpu"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), env.get("PYTHONPATH")]))
    result = subprocess.run([sys.executable, "-X", "faulthandler", "-c", script, str(tmp_path / "data")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
