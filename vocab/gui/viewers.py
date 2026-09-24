"""In-app document viewers: native PDF (QtPdf) and EPUB (QtWebEngine).

No third-party deps. EPUB is parsed with stdlib zipfile + xml.etree:
  META-INF/container.xml -> OPF path -> <manifest>/<spine> -> ordered XHTML.
We extract the epub to a temp dir and point QWebEngineView at each chapter file
so relative CSS/images/fonts resolve exactly as an ebook reader would.

Both viewers are pane components: TITLE / refresh() / focus_default().
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from urllib.parse import unquote, urlsplit
from pathlib import Path
from xml.etree import ElementTree as ET

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)

from . import theme


# --------------------------------------------------------------------------- #
#  EPUB parsing (stdlib only)
# --------------------------------------------------------------------------- #
_OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "cnt": "urn:oasis:names:tc:opendocument:xmlns:container",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class EpubBook:
    """Extracted EPUB with an ordered list of (title, absolute_file_path)."""

    def __init__(self, path: str):
        self.src = path
        self._temp = tempfile.TemporaryDirectory(prefix="vocab_epub_")
        self.tmpdir = self._temp.name
        self.title = os.path.splitext(os.path.basename(path))[0]
        self.chapters: list[tuple[str, str]] = []
        try:
            self._extract_and_parse(path)
        except Exception:
            self._temp.cleanup()
            raise

    def _inside(self, base, relative):
        url = urlsplit(relative)
        if url.scheme or url.netloc:
            raise ValueError("EPUB entries must refer to files inside the book.")
        path = (Path(base) / unquote(url.path).replace("\\", "/")).resolve()
        try:
            path.relative_to(Path(self.tmpdir).resolve())
        except ValueError:
            raise ValueError("EPUB contains a path outside the book.") from None
        return str(path)

    def _extract_and_parse(self, path: str) -> None:
        with zipfile.ZipFile(path) as z:
            for entry in z.infolist():
                self._inside(self.tmpdir, entry.filename)
            z.extractall(self.tmpdir)
        # 1) container.xml -> OPF path
        container = os.path.join(self.tmpdir, "META-INF", "container.xml")
        opf_rel = None
        if os.path.exists(container):
            root = ET.parse(container).getroot()
            rf = root.find(".//cnt:rootfile", _OPF_NS)
            if rf is not None:
                opf_rel = rf.get("full-path")
        if not opf_rel:  # fallback: first .opf we can find
            for base, _dirs, files in os.walk(self.tmpdir):
                for f in files:
                    if f.endswith(".opf"):
                        opf_rel = os.path.relpath(os.path.join(base, f), self.tmpdir)
                        break
                if opf_rel:
                    break
        if not opf_rel:
            raise ValueError("Not a valid EPUB (no OPF package found)")

        opf_path = self._inside(self.tmpdir, opf_rel)
        opf_dir = os.path.dirname(opf_path)
        tree = ET.parse(opf_path).getroot()

        # title (nice-to-have)
        t = tree.find(".//dc:title", _OPF_NS)
        if t is not None and t.text:
            self.title = t.text.strip()

        # 2) manifest: id -> href
        manifest: dict[str, str] = {}
        for item in tree.findall(".//opf:manifest/opf:item", _OPF_NS):
            manifest[item.get("id")] = item.get("href")

        # 3) spine: ordered idrefs -> files
        for it in tree.findall(".//opf:spine/opf:itemref", _OPF_NS):
            idref = it.get("idref")
            href = manifest.get(idref)
            if not href:
                continue
            fpath = self._inside(opf_dir, href)
            if os.path.exists(fpath):
                self.chapters.append((self._nice_name(href), fpath))

        if not self.chapters:
            # last resort: any xhtml/html in reading-ish order
            for base, _dirs, files in os.walk(self.tmpdir):
                for f in sorted(files):
                    if f.lower().endswith((".xhtml", ".html", ".htm")):
                        self.chapters.append((f, os.path.join(base, f)))

    @staticmethod
    def _nice_name(href: str) -> str:
        return os.path.splitext(os.path.basename(href))[0].replace("_", " ")


# --------------------------------------------------------------------------- #
#  Viewer components
# --------------------------------------------------------------------------- #
class PdfViewer(QWidget):
    """Native paged PDF viewer via QtPdf."""

    back_requested = Signal()

    def __init__(self, path: str, ctx=None, parent=None):
        super().__init__(parent)
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView

        self.path = path
        self.TITLE = f"PDF · {os.path.basename(path)}"
        self.ctx = ctx
        self._module = getattr(getattr(ctx, "app", None), "current_module", None)
        self._storage = getattr(getattr(ctx, "app", None), "storage", None)
        self._fname = os.path.basename(path)
        self._doc = QPdfDocument(self)
        error = self._doc.load(path)
        if error != QPdfDocument.Error.None_:
            raise ValueError(f"Could not read PDF: {error.name}")
        self._view = QPdfView(self)
        self._view.setDocument(self._doc)
        self._view.setPageMode(QPdfView.PageMode.MultiPage)   # continuous scroll
        self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        back = QPushButton("‹ Documents"); back.clicked.connect(self.back_requested.emit)
        self._info = QLabel(objectName="Muted")
        zin = QPushButton("A+"); zin.setToolTip("Zoom in"); zin.clicked.connect(lambda: self._zoom(1.25))
        zout = QPushButton("A−"); zout.setToolTip("Zoom out"); zout.clicked.connect(lambda: self._zoom(0.8))
        fit = QPushButton("Fit width"); fit.clicked.connect(self._fit)
        ext = QPushButton("Open externally"); ext.clicked.connect(self._open_ext)
        bar.addWidget(back)
        bar.addWidget(self._info, 1)
        for b in (zout, zin, fit, ext):
            bar.addWidget(b)
        for b in (back, zout, zin, fit, ext):
            b.setFocusPolicy(Qt.NoFocus)
        lay.addLayout(bar)
        lay.addWidget(self._view, 1)

        self._doc.statusChanged.connect(self._update_info)
        self._doc.statusChanged.connect(self._restore_when_ready)
        self._update_info()
        self._restore_when_ready(self._doc.status())

    def _restore_when_ready(self, status) -> None:
        from PySide6.QtPdf import QPdfDocument
        if status != QPdfDocument.Status.Ready:
            return
        if not (self._storage and self._module):
            return
        try:
            saved = self._storage.load_reading_pos(self._module, self._fname)
        except Exception:
            saved = {}
        if "zoom" in saved:
            try:
                self._view.setZoomMode(self._view.ZoomMode.Custom)
                self._view.setZoomFactor(float(saved["zoom"]))
            except (TypeError, ValueError):
                pass
        try:
            page = int(saved.get("page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 0:
            try:
                nav = self._view.pageNavigator()
                from PySide6.QtCore import QPointF
                nav.jump(min(page, self._doc.pageCount() - 1), QPointF(0, 0))
            except Exception:
                pass

    def save_position(self) -> None:
        if not (self._storage and self._module):
            return
        try:
            page = self._view.pageNavigator().currentPage()
        except Exception:
            page = 0
        try:
            zoom = float(self._view.zoomFactor())
        except Exception:
            zoom = 1.0
        try:
            self._storage.save_reading_pos(self._module, self._fname,
                                           {"page": int(page), "zoom": zoom})
        except Exception:
            pass

    def closeEvent(self, e):
        self.save_position()
        super().closeEvent(e)

    def on_replaced(self) -> None:
        """Called by Pane.set_component when this viewer is swapped out."""
        self.save_position()

    def _update_info(self, *_):
        n = self._doc.pageCount()
        self._info.setText(f"{os.path.basename(self.path)}  ·  {n} page(s)")

    def _zoom(self, factor: float):
        self._view.setZoomMode(self._view.ZoomMode.Custom)
        self._view.setZoomFactor(max(0.1, min(5.0, self._view.zoomFactor() * factor)))

    def _fit(self):
        from PySide6.QtPdfWidgets import QPdfView
        self._view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def _open_ext(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def refresh(self) -> None:
        pass

    def focus_default(self) -> None:
        self._view.setFocus()


class EpubViewer(QWidget):
    """EPUB reader: QtWebEngine per-chapter with prev/next + chapter picker."""

    back_requested = Signal()
    ZOOM_STEP = 1.15
    ZOOM_MIN = 0.5
    ZOOM_MAX = 3.0

    def __init__(self, path: str, ctx=None, parent=None):
        super().__init__(parent)
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.path = path
        self.ctx = ctx
        # module + storage for persisting reading position (best-effort)
        self._module = getattr(getattr(ctx, "app", None), "current_module", None)
        self._storage = getattr(getattr(ctx, "app", None), "storage", None)
        self._fname = os.path.basename(path)
        self.TITLE = f"EPUB · {os.path.basename(path)}"
        self._idx = 0
        self._zoom = 1.25   # start a bit larger — default EPUB text runs small
        self._pending_scroll = 0.0   # scroll to restore once the chapter loads
        self._book = EpubBook(path)
        if self._book.title:
            self.TITLE = f"EPUB · {self._book.title}"

        # restore any saved position BEFORE building/loading
        saved = {}
        if self._storage and self._module:
            try:
                saved = self._storage.load_reading_pos(self._module, self._fname)
            except Exception:
                saved = {}
        try:
            start_idx = int(saved.get("chapter", 0))
        except (TypeError, ValueError):
            start_idx = 0
        if "zoom" in saved:
            try:
                self._zoom = float(saved["zoom"])
            except (TypeError, ValueError):
                pass
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom))
        try:
            self._pending_scroll = max(0.0, min(1.0, float(saved.get("scroll", 0.0) or 0.0)))
        except (TypeError, ValueError):
            self._pending_scroll = 0.0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        back = QPushButton("‹ Documents"); back.clicked.connect(self.back_requested.emit)
        self._prev = QPushButton("‹ Prev"); self._prev.clicked.connect(lambda: self._go(-1))
        self._next = QPushButton("Next ›"); self._next.clicked.connect(lambda: self._go(1))
        self._picker = QComboBox()
        for i, (name, _p) in enumerate(self._book.chapters):
            self._picker.addItem(f"{i + 1}. {name}")
        self._picker.currentIndexChanged.connect(self._jump)
        fdown = QPushButton("A−"); fdown.setToolTip("Smaller text"); fdown.clicked.connect(lambda: self._bump(1 / self.ZOOM_STEP))
        fup = QPushButton("A+"); fup.setToolTip("Larger text"); fup.clicked.connect(lambda: self._bump(self.ZOOM_STEP))
        addw = QPushButton("＋ Add word")
        addw.setToolTip("Add the highlighted word to this module's vocab (with its sentence for context)")
        addw.clicked.connect(self._add_selection)
        ext = QPushButton("Open externally"); ext.clicked.connect(self._open_ext)
        bar.addWidget(back)
        bar.addWidget(self._prev)
        bar.addWidget(self._next)
        bar.addWidget(self._picker, 1)
        bar.addWidget(addw)
        bar.addWidget(fdown)
        bar.addWidget(fup)
        bar.addWidget(ext)
        for w in (back, self._prev, self._next, self._picker, fdown, fup, addw, ext):
            w.setFocusPolicy(Qt.NoFocus)
        lay.addLayout(bar)

        self._web = QWebEngineView(self)
        lay.addWidget(self._web, 1)
        # re-apply zoom + restore scroll whenever a chapter finishes loading
        self._web.loadFinished.connect(self._on_loaded)

        if self._book.chapters:
            self._load(max(0, min(start_idx, len(self._book.chapters) - 1)))
        else:
            self._web.setHtml("<body style='background:#1f1f1f;color:#eee;"
                              "font-family:sans-serif;padding:2em'>"
                              "<h3>Could not read this EPUB's chapters.</h3></body>")
        self._apply_zoom()

    def _on_loaded(self, _ok: bool) -> None:
        self._apply_zoom()
        if self._pending_scroll:
            # restore vertical scroll fraction, then clear it
            frac = self._pending_scroll
            self._pending_scroll = 0.0
            self._web.page().runJavaScript(
                f"window.scrollTo(0, document.body.scrollHeight * {frac});")

    def _bump(self, factor: float) -> None:
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        self._web.setZoomFactor(self._zoom)

    def _load(self, i: int):
        if not (0 <= i < len(self._book.chapters)):
            return
        self._idx = i
        _name, fpath = self._book.chapters[i]
        # Load the chapter bytes and hand them to Chromium as HTML (text/html),
        # NOT by URL — loading an .xhtml by URL makes QtWebEngine treat it as raw
        # XML and show the source tree ("no style information") instead of the
        # rendered page. A base URL (the chapter's own file URL) keeps relative
        # CSS / images / fonts resolving correctly.
        from PySide6.QtCore import QByteArray
        try:
            with open(fpath, "rb") as fh:
                data = fh.read()
        except OSError as e:
            self._web.setHtml(f"<body style='color:#eee;background:#1f1f1f;padding:2em'>"
                              f"Could not read chapter: {e}</body>")
        else:
            base = QUrl.fromLocalFile(fpath)
            self._web.setContent(QByteArray(data), "text/html;charset=utf-8", base)
        # zoom factor can reset on new content — re-apply immediately (a one-time
        # loadFinished handler wired in __init__ re-applies after it settles too)
        self._apply_zoom()
        self._picker.blockSignals(True)
        self._picker.setCurrentIndex(i)
        self._picker.blockSignals(False)
        self._prev.setEnabled(i > 0)
        self._next.setEnabled(i < len(self._book.chapters) - 1)

    def _go(self, delta: int):
        self._load(self._idx + delta)

    def _jump(self, i: int):
        if i != self._idx:
            self._load(i)

    def _open_ext(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    # JS: get the selected word + the sentence it sits in (from the selection's
    # surrounding text). Splits on sentence punctuation, keeps the piece
    # containing the selection.
    _SEL_JS = r"""
    (function () {
      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return "";
      var word = (sel.toString() || "").trim();
      if (!word) return "";
      var node = sel.anchorNode;
      var container = node && node.nodeType === 3 ? node.parentElement : node;
      var block = container;
      while (block && block.textContent && block.textContent.length < 400 &&
             block.parentElement) block = block.parentElement;
      var text = (block ? block.textContent : (container ? container.textContent : word)) || word;
      text = text.replace(/\s+/g, " ").trim();
      // find the sentence containing the (first occurrence of the) word
      var parts = text.split(/(?<=[.!?])\s+/);
      var sentence = text;
      for (var i = 0; i < parts.length; i++) {
        if (parts[i].toLowerCase().indexOf(word.toLowerCase()) !== -1) { sentence = parts[i]; break; }
      }
      return JSON.stringify({word: word, sentence: sentence.trim()});
    })();
    """

    def _add_selection(self):
        def _done(payload):
            import json as _json
            if not payload:
                if self.ctx:
                    self.ctx.log.emit("! Highlight a word in the text first, then click Add word.")
                return
            try:
                data = _json.loads(payload)
            except Exception:
                return
            word = (data.get("word") or "").strip()
            sentence = (data.get("sentence") or "").strip()
            # a single word only (ignore multi-word selections for vocab)
            word = word.split()[0] if word else ""
            # strip surrounding punctuation
            word = word.strip(".,;:!?\"'()[]“”‘’")
            if not word:
                return
            if self.ctx:
                self.ctx.add_word_requested.emit(word, sentence, self._module or "")
        try:
            self._web.page().runJavaScript(self._SEL_JS, _done)
        except Exception:
            pass

    def save_position(self) -> None:
        """Read Qt's cached scroll position synchronously, including on close."""
        if not (self._storage and self._module):
            return
        page = self._web.page()
        height = max(1.0, page.contentsSize().height())
        fraction = self._pending_scroll or max(0.0, min(1.0, page.scrollPosition().y() / height))
        self._storage.save_reading_pos(
            self._module, self._fname,
            {"chapter": self._idx, "scroll": fraction, "zoom": self._zoom})

    def refresh(self) -> None:
        pass

    def focus_default(self) -> None:
        self._web.setFocus()

    def closeEvent(self, e):
        self.save_position()
        super().closeEvent(e)

    def on_replaced(self) -> None:
        """Called by Pane.set_component when this viewer is swapped out."""
        self.save_position()


# --------------------------------------------------------------------------- #
def make_viewer(path: str, ctx=None):
    """Return the right viewer for a file, or None if unsupported."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return PdfViewer(path, ctx)
    if ext == ".epub":
        return EpubViewer(path, ctx)
    return None
