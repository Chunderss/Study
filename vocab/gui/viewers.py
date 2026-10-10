"""In-app document viewers: native PDF (QtPdf) and EPUB (QtWebEngine).

No third-party deps. EPUB is parsed with stdlib zipfile + xml.etree:
  META-INF/container.xml -> OPF path -> <manifest>/<spine> -> ordered XHTML.
We extract the epub to a temp dir and point QWebEngineView at each chapter file
so relative CSS/images/fonts resolve exactly as an ebook reader would.

Both viewers are pane components: TITLE / refresh() / focus_default().
"""
from __future__ import annotations

import html.entities
import json
import os
import re
import shutil
import tempfile
import zipfile
import xml.parsers.expat
from urllib.parse import quote as quote_url, unquote, urlsplit
from pathlib import Path
from xml.etree import ElementTree as ET

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl, Signal
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPushButton,
                               QVBoxLayout, QWidget)
from shiboken6 import isValid

from . import theme
from .epub_highlights import SELECTION_ANCHOR_JS, render_highlights_js
from ..core.learning import LearningStore
from ..core.storage import ModuleNotFound


# --------------------------------------------------------------------------- #
#  EPUB parsing (stdlib only)
# --------------------------------------------------------------------------- #
_OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "cnt": "urn:oasis:names:tc:opendocument:xmlns:container",
    "dc": "http://purl.org/dc/elements/1.1/",
}


_XHTML_NS = "http://www.w3.org/1999/xhtml"
# Blink resolves the XHTML DTD's named entities (&nbsp;) only for these.
_XHTML_PUBLIC_IDS = {
    "-//W3C//DTD XHTML 1.0 Transitional//EN", "-//W3C//DTD XHTML 1.0 Strict//EN",
    "-//W3C//DTD XHTML 1.0 Frameset//EN", "-//W3C//DTD XHTML 1.1//EN",
    "-//W3C//DTD XHTML Basic 1.0//EN", "-//W3C//DTD XHTML 1.1 plus MathML 2.0//EN",
    "-//W3C//DTD XHTML 1.1 plus MathML 2.0 plus SVG 1.1//EN",
    "-//WAPFORUM//DTD XHTML Mobile 1.0//EN",
}
_ENTITY_REFERENCE = re.compile(r"&([A-Za-z_:][\w.:-]*);")
_XML_ENTITIES = {"lt", "gt", "amp", "quot", "apos"}


def _renders_as_xhtml(source: Path) -> bool:
    """Whether a chapter is well-formed XHTML that Chromium's XML parser can
    show. Only that parser keeps self-closing tags such as <title/> and
    <a id="x"/> closed; the HTML parser ignores the slash and swallows the
    rest of the chapter. Anything else keeps the HTML parser's tolerance."""
    try:
        data = source.read_bytes()
    except OSError:
        return False
    parser = xml.parsers.expat.ParserCreate(namespace_separator=" ")
    found = {"root": None, "doctype": None, "encoding": None}

    def start(name, _attributes):
        if found["root"] is None:
            found["root"] = name

    parser.StartElementHandler = start
    parser.StartDoctypeDeclHandler = lambda _name, _system, public, _internal: found.update(doctype=public)
    parser.XmlDeclHandler = lambda _version, encoding, _standalone: found.update(encoding=encoding)
    try:
        parser.Parse(data, True)
    except (xml.parsers.expat.ExpatError, ValueError):
        return False
    if found["root"] != f"{_XHTML_NS} html":
        return False
    # Only UTF-8 and UTF-16. QtWebEngine 6.11's renderer crashes on some
    # legacy charsets, and Chromium does not know every label Python does;
    # those chapters stay on the HTML path, decoded as UTF-8 as before.
    declared = (found["encoding"] or "utf-8").lower().replace("_", "-")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        if declared not in ("utf-8", "utf-16"):
            return False
        encoding = "utf-16"
    elif declared in ("utf-8", "utf8"):
        encoding = "utf-8-sig"
    else:
        return False
    try:
        text = data.decode(encoding)
    except UnicodeDecodeError:
        return False
    # Named entities anywhere, attribute values included (expat does not
    # report those), must be ones Chromium resolves for the doctype.
    names = set(_ENTITY_REFERENCE.findall(text)) - _XML_ENTITIES
    return not names or (found["doctype"] in _XHTML_PUBLIC_IDS
                         and names <= set(html.entities.name2codepoint))


class EpubBook:
    """Extracted EPUB with an ordered list of (title, absolute_file_path)."""

    def __init__(self, path: str):
        self.src = path
        self._temp = tempfile.TemporaryDirectory(prefix="vocab_epub_")
        # Windows temp paths may use an 8.3 username while resolved chapter
        # paths use the long name. Keep one canonical root for relative paths.
        self.tmpdir = str(Path(self._temp.name).resolve())
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
    ZOOM_MIN = 0.1
    ZOOM_MAX = 5.0

    def __init__(self, path: str, ctx=None, parent=None, module=None):
        super().__init__(parent)
        from PySide6.QtPdf import QPdfDocument
        from PySide6.QtPdfWidgets import QPdfView

        self.path = path
        self.TITLE = f"PDF · {os.path.basename(path)}"
        self.ctx = ctx
        self._module = module if module is not None else getattr(getattr(ctx, "app", None), "current_module", None)
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
        back = self._back = QPushButton("‹ Documents"); back.clicked.connect(self.back_requested.emit)
        self._info = QLabel(objectName="Muted")
        zin = QPushButton("A+"); zin.setToolTip("Zoom in"); zin.clicked.connect(lambda: self._zoom(1.25))
        zout = QPushButton("A−"); zout.setToolTip("Zoom out"); zout.clicked.connect(lambda: self._zoom(0.8))
        fit = QPushButton("Fit width"); fit.clicked.connect(self._fit)
        ext = QPushButton("Open externally"); ext.clicked.connect(self._open_ext)
        capture = QPushButton("Capture page")
        capture.setToolTip("Capture text from this PDF page, then trim it to a passage and add a question")
        capture.clicked.connect(self._capture)
        bar.addWidget(back)
        bar.addWidget(self._info, 1)
        lay.addLayout(bar)
        actions = QHBoxLayout()
        actions.setContentsMargins(6, 0, 6, 4)
        for b in (capture, zout, zin, fit, ext):
            actions.addWidget(b)
        for b in (back, capture, zout, zin, fit, ext):
            b.setFocusPolicy(Qt.NoFocus)
        lay.addLayout(actions)
        lay.addWidget(self._view, 1)

        # A page requested before the view has its final size (Source, a saved
        # position) is applied again whenever layout changes the scroll range,
        # until the user scrolls.
        self._pending_page = None
        bar = self._view.verticalScrollBar()
        bar.rangeChanged.connect(self._reapply_page)
        bar.actionTriggered.connect(self._user_scrolled)
        bar.installEventFilter(self)  # its menu ("Scroll here") moves without an action
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
        # Fit width is saved as a mode; a number is a custom zoom. Older
        # versions saved Fit width as its stale factor 1.0.
        legacy_fit = "zoom_mode" not in saved and saved.get("zoom") == 1.0
        if saved.get("zoom_mode") != "fit_width" and not legacy_fit and "zoom" in saved:
            try:
                zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, float(saved["zoom"])))
                self._view.setZoomMode(self._view.ZoomMode.Custom)
                self._view.setZoomFactor(zoom)
            except (TypeError, ValueError):
                pass
        try:
            page = int(saved.get("page", 0) or 0)
        except (TypeError, ValueError):
            page = 0
        if page > 0 and self._pending_page is None:  # a Source target wins
            self._jump(min(page, self._doc.pageCount() - 1))

    def _jump(self, page):
        self._pending_page = page
        self._show_page(page)

    def _show_page(self, page):
        from PySide6.QtCore import QPointF
        nav = self._view.pageNavigator()
        if nav.currentPage() == page and self._doc.pageCount() > 1:
            # QPdfView scrolls only when the page changes: step away first, so
            # the page is shown from its top after a relayout too.
            nav.jump(page - 1 if page > 0 else page + 1, QPointF(0, 0))
        nav.jump(page, QPointF(0, 0))

    def _reapply_page(self, *_):
        if self._pending_page is not None:
            # Let the layout finish first; it resets the scroll position.
            QTimer.singleShot(0, self, self._apply_pending_page)

    def _apply_pending_page(self):
        if self._pending_page is not None and isValid(self._view):
            self._show_page(self._pending_page)

    def _user_scrolled(self, _action):
        self._pending_page = None

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ContextMenu and watched is self._view.verticalScrollBar():
            self._pending_page = None
        return False

    def _current_page(self):
        if self._pending_page is not None:
            return self._pending_page
        return max(0, self._view.pageNavigator().currentPage())

    def save_position(self) -> None:
        if not (self._storage and self._module):
            return
        try:
            page = self._current_page()
        except Exception:
            page = 0
        position = {"page": int(page)}
        if self._view.zoomMode() == self._view.ZoomMode.FitToWidth:
            position["zoom_mode"] = "fit_width"  # zoomFactor() is stale in this mode
        else:
            position["zoom"] = float(self._view.zoomFactor())
        # A failure is the caller's to report: the reader stays open to retry.
        self._storage.save_reading_pos(self._module, self._fname, position)

    def set_back_label(self, text):
        self._back.setText(text)

    def source_location(self):
        return {"kind": "pdf", "filename": self._fname, "page": self._current_page()}

    def _capture(self):
        if self.ctx:
            source = self.source_location()
            quote = self._doc.getAllText(source["page"]).text()
            self.ctx.capture_requested.emit(self._module or "", quote, source)

    def go_to(self, source):
        self._jump(max(0, min(int(source.get("page", 0)), self._doc.pageCount() - 1)))

    def closeEvent(self, e):
        _save_on_close(self)
        super().closeEvent(e)

    def on_replaced(self) -> None:
        """Called by Pane.set_component when this viewer is swapped out. If the
        position cannot be saved this raises, and the viewer stays usable."""
        self.save_position()
        self.release()

    def release(self) -> None:
        self._doc.close()  # release the file now, not only when deleted

    def _update_info(self, *_):
        n = self._doc.pageCount()
        self._info.setText(f"{os.path.basename(self.path)}  ·  {n} page(s)")

    def _effective_zoom(self) -> float:
        """The scale pages are shown at. In Fit width mode zoomFactor() keeps
        its last custom value, so compute the scale as QPdfView lays it out."""
        view = self._view
        if view.zoomMode() == view.ZoomMode.Custom:
            return view.zoomFactor()
        from PySide6.QtGui import QGuiApplication
        screen = QGuiApplication.primaryScreen()
        resolution = (screen.logicalDotsPerInch() if screen else 72.0) / 72.0
        width = self._doc.pagePointSize(self._current_page()).width() * resolution
        margins = view.documentMargins()
        available = view.viewport().width() - margins.left() - margins.right()
        return available / width if width > 0 and available > 0 else view.zoomFactor()

    def _zoom(self, factor: float):
        current = self._effective_zoom()
        # Fit width can exceed the limits; a step never goes the other way.
        low, high = min(self.ZOOM_MIN, current), max(self.ZOOM_MAX, current)
        self._view.setZoomMode(self._view.ZoomMode.Custom)
        self._view.setZoomFactor(max(low, min(high, current * factor)))

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
    MAX_RESTORE_RETRIES = 20

    def __init__(self, path: str, ctx=None, parent=None, module=None, source=None):
        super().__init__(parent)
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.path = path
        self.ctx = ctx
        # module + storage for persisting reading position (best-effort)
        self._module = module if module is not None else getattr(getattr(ctx, "app", None), "current_module", None)
        self._storage = getattr(getattr(ctx, "app", None), "storage", None)
        self._fname = os.path.basename(path)
        self.TITLE = f"EPUB · {os.path.basename(path)}"
        self._idx = 0
        self._zoom = 1.25   # start a bit larger — default EPUB text runs small
        # A scroll fraction to restore once the chapter loads (0.0 is the top);
        # None when the live position applies.
        self._pending_scroll = None
        self._requested_scroll = 0.0  # reported while a requested chapter loads
        # Qt's cached scroll can still hold the previous document's value for a
        # moment after a load; it is used once it has changed for this one.
        self._stale_scroll = 0.0
        self._scroll_fresh = False
        self._loaded_path = None  # the last document that finished loading
        # Nothing is saved until a chapter was requested successfully, so a
        # reader whose first load failed keeps the previously saved place.
        self._position_known = False
        self._book = EpubBook(path)
        self._chapter_paths = {str(Path(p).resolve()): i
                               for i, (_name, p) in enumerate(self._book.chapters)}
        self._html_paths = {}
        self._source_paths = {}
        self._current_path = None
        self._requested_url = QUrl()
        self._load_generation = 0
        self._document_loaded = False
        self._active_capture_id = None
        self._source_highlight = None
        self._pending_highlight = None
        self._set_highlight_source(source)
        if self._book.title:
            self.TITLE = f"EPUB · {self._book.title}"

        # restore any saved position BEFORE building/loading
        saved = {}
        if self._storage and self._module:
            try:
                saved = self._storage.load_reading_pos(self._module, self._fname)
            except Exception:
                saved = {}
        # A capture supplies the initial location, while saved preferences still
        # provide zoom. Load once so an outgoing navigation cannot reset the target.
        location = saved if source is None else source
        try:
            start_idx = int(location.get("chapter", 0))
        except (TypeError, ValueError):
            start_idx = 0
        if "zoom" in saved:
            try:
                self._zoom = float(saved["zoom"])
            except (TypeError, ValueError):
                pass
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom))
        try:
            if "scroll" in location:
                self._pending_scroll = max(0.0, min(1.0, float(location["scroll"] or 0.0)))
        except (TypeError, ValueError):
            self._pending_scroll = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        bar = QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        back = self._back = QPushButton("‹ Documents"); back.clicked.connect(self.back_requested.emit)
        self._prev = QPushButton("‹ Prev"); self._prev.clicked.connect(lambda: self._go(-1))
        self._next = QPushButton("Next ›"); self._next.clicked.connect(lambda: self._go(1))
        self._picker = QComboBox()
        for i, (name, _p) in enumerate(self._book.chapters):
            self._picker.addItem(f"{i + 1}. {name}")
        self._picker.currentIndexChanged.connect(self._jump)
        # Choosing the chapter already selected must still work: from a page
        # outside the reading order (a footnote) it returns to the chapter.
        self._picker.activated.connect(self._jump)
        fdown = QPushButton("A−"); fdown.setToolTip("Smaller text"); fdown.clicked.connect(lambda: self._bump(1 / self.ZOOM_STEP))
        fup = QPushButton("A+"); fup.setToolTip("Larger text"); fup.clicked.connect(lambda: self._bump(self.ZOOM_STEP))
        addw = QPushButton("＋ Add word")
        addw.setToolTip("Add the highlighted word to this module's vocab (with its sentence for context)")
        addw.clicked.connect(self._add_selection)
        capture = QPushButton("Capture")
        capture.setToolTip("Save this passage with a question and keep it highlighted in the book")
        capture.clicked.connect(self._capture)
        ext = QPushButton("Open externally"); ext.clicked.connect(self._open_ext)
        bar.addWidget(back)
        bar.addWidget(self._prev)
        bar.addWidget(self._next)
        bar.addWidget(self._picker, 1)
        lay.addLayout(bar)
        actions = QHBoxLayout()
        actions.setContentsMargins(6, 0, 6, 4)
        for w in (addw, capture, fdown, fup, ext):
            actions.addWidget(w)
        for w in (back, self._prev, self._next, self._picker, fdown, fup, addw, capture, ext):
            w.setFocusPolicy(Qt.NoFocus)
        lay.addLayout(actions)

        self._error = QLabel()
        self._error.setTextFormat(Qt.PlainText)
        self._error.setWordWrap(True)
        self._error.hide()
        lay.addWidget(self._error)
        self._web = QWebEngineView(self)
        # Books are untrusted: their scripts could read local files through the
        # file:// chapter pages and send them out. Disable them before the first
        # load. The reader's own scripts run in an isolated world (_run_js).
        from PySide6.QtWebEngineCore import QWebEngineSettings
        settings = self._web.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self._web.page().navigationRequested.connect(self._navigate)
        self._web.page().scrollPositionChanged.connect(self._scroll_changed)
        self._web.page().loadingChanged.connect(self._loading_changed)
        lay.addWidget(self._web, 1)
        self._web.installEventFilter(self)
        if self.ctx:
            self.ctx.learning_changed.connect(self._refresh_highlights)

        if self._book.chapters:
            self._load(max(0, min(start_idx, len(self._book.chapters) - 1)), href=location.get("href"))
        else:
            self._show_error("Could not read this EPUB's chapters.")
            self._prev.setEnabled(False)
            self._next.setEnabled(False)
        self._apply_zoom()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.KeyPress, QEvent.Wheel, QEvent.TouchBegin, QEvent.MouseButtonPress):
            return False
        if kind == QEvent.KeyPress and event.key() not in (
                Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right,
                Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End, Qt.Key_Space):
            return False
        if kind == QEvent.KeyPress:
            modifiers = event.modifiers()
            if modifiers & (Qt.AltModifier | Qt.MetaModifier):
                return False
            if modifiers & Qt.ControlModifier and event.key() not in (Qt.Key_Home, Qt.Key_End):
                return False
        if (self._document_loaded and isValid(self._web) and isinstance(watched, QWidget) and
                (watched is self._web or self._web.isAncestorOf(watched))):
            # Explicit reader interaction takes precedence over saved-position retries.
            self._pending_scroll = None
            self._pending_highlight = None
        return False

    def showEvent(self, event):
        super().showEvent(event)
        if self._document_loaded:
            generation = self._load_generation
            QTimer.singleShot(0, self, lambda: self._restore_location(generation))

    def _begin_document(self, scroll):
        """A new document is requested: until its own position is known,
        report ``scroll``, never the outgoing document's cached one."""
        self._document_loaded = False
        self._requested_scroll = scroll
        self._stale_scroll = self._web.page().scrollPosition().y()
        self._scroll_fresh = False

    def _scroll_changed(self, _position):
        if self._document_loaded:
            self._scroll_fresh = True

    def _on_loaded(self) -> None:
        self._document_loaded = True
        self._loaded_path = Path(self._requested_url.toLocalFile()).resolve()
        if self._web.page().scrollPosition().y() != self._stale_scroll:
            self._scroll_fresh = True  # the cache already shows this document
        self._web.show()
        # Chromium delivers input to its child widgets, including the focus proxy.
        # Refresh these filters after loading in case the renderer replaced them.
        for child in self._web.findChildren(QWidget):
            child.installEventFilter(self)
        self._apply_zoom()
        generation = self._load_generation
        QTimer.singleShot(0, self, lambda: self._restore_location(generation))

    def _set_highlight_source(self, source):
        source = source or {}
        capture_id = source.get("capture_id")
        anchor = source.get("anchor")
        if not isinstance(anchor, dict) and source.get("quote"):
            anchor = {"exact": source["quote"]}
        self._active_capture_id = capture_id or ("source" if anchor else None)
        self._source_highlight = ({"id": "source", "anchor": anchor}
                                  if anchor and not capture_id else None)
        self._pending_highlight = self._active_capture_id

    def _restore_location(self, generation, attempt=0):
        if isValid(self) and generation == self._load_generation:
            self._refresh_highlights(restore=True, attempt=attempt)

    def _highlight_records(self):
        records = []
        if self._storage and self._module:
            try:
                items = LearningStore(self._storage).load(self._module)
                for item in items.values():
                    source = item["source"]
                    if source.get("kind") != "epub" or source.get("filename") != self._fname:
                        continue
                    try:
                        if source.get("href") is not None:
                            if not isinstance(source["href"], str):
                                continue
                            path = self._book._inside(self._book.tmpdir, source["href"])
                        else:
                            chapter = int(source.get("chapter", 0))
                            if not 0 <= chapter < len(self._book.chapters):
                                continue
                            path = self._book.chapters[chapter][1]
                        if str(Path(path).resolve()) != self._current_path:
                            continue
                    except (TypeError, ValueError):
                        continue
                    anchor = source.get("anchor")
                    records.append({"id": item["id"], "anchor": anchor if isinstance(anchor, dict)
                                    else {"exact": item["quote"]}})
            except (OSError, ValueError, ModuleNotFound) as error:
                if self.ctx:
                    self.ctx.log.emit(f"Could not load EPUB highlights: {error}")
        if self._source_highlight:
            records.append(self._source_highlight)
        return records

    def _refresh_highlights(self, *, restore=False, attempt=0):
        if not self._document_loaded or not isValid(self._web):
            return
        # A hidden-load reply may arrive after showEvent starts its own restore.
        # Only the request made with a visible viewport can consume that target.
        restore = restore and self._web.isVisible()
        generation = self._load_generation
        pending = self._pending_highlight if restore else None
        script = render_highlights_js(self._highlight_records(), self._active_capture_id, pending)
        expected = json.dumps(self._requested_url.toString(QUrl.FullyEncoded))
        viewport = json.dumps({"width": self._web.width() / self._zoom,
                               "height": self._web.height() / self._zoom})

        def rendered(result):
            if not isValid(self) or generation != self._load_generation or not restore:
                return
            if not self._web.isVisible():
                return
            if pending and pending == self._pending_highlight:
                try:
                    report = json.loads(result)
                except (TypeError, ValueError):
                    report = {}
                if report.get("ready") is False and attempt < self.MAX_RESTORE_RETRIES:
                    QTimer.singleShot(16, self, lambda: self._restore_location(generation, attempt + 1))
                    return
                self._pending_highlight = None
                if report.get("focused", False):
                    self._pending_scroll = None
            # A missing passage falls back to the capture's saved scroll location.
            self._restore_scroll(generation)

        self._run_js(f"""
            (() => {{
                const expected = new URL({expected});
                const current = new URL(location.href);
                expected.hash = current.hash = '';
                if (current.href !== expected.href || document.readyState !== 'complete')
                    return '';
                const viewport = {viewport};
                // Qt can reveal a pane before Chromium receives its final size.
                // Focusing in the old tiny viewport moves again during layout.
                if ({json.dumps(bool(pending))} && (document.visibilityState === 'hidden' ||
                        Math.abs(innerWidth - viewport.width) > 2 ||
                        Math.abs(innerHeight - viewport.height) > 2))
                    return JSON.stringify({{ready: false}});
                return {script.rstrip().rstrip(';')};
            }})();
        """, rendered)

    def _run_js(self, script, callback):
        """Run reader code in Qt's ApplicationWorld.

        It shares the book's DOM but not its JavaScript globals, and it still
        runs while the book's own scripts are disabled.
        """
        from PySide6.QtWebEngineCore import QWebEngineScript
        self._web.page().runJavaScript(
            script, QWebEngineScript.ScriptWorldId.ApplicationWorld.value, callback)

    def _restore_scroll(self, generation, attempt=0):
        if not isValid(self) or generation != self._load_generation or self._pending_scroll is None:
            return
        # Hidden WebEngine views have no reliable viewport or cached scroll geometry.
        # Preserve the saved position until showEvent can restore it visibly.
        if not self._web.isVisible():
            return
        expected = json.dumps(self._requested_url.toString(QUrl.FullyEncoded))
        fraction = self._pending_scroll

        def restored(applied):
            if not isValid(self) or generation != self._load_generation or fraction != self._pending_scroll:
                return
            if not self._web.isVisible():
                return
            if applied or attempt >= self.MAX_RESTORE_RETRIES:
                # Once retries end, captures and saves must use the live position.
                self._pending_scroll = None
            else:
                QTimer.singleShot(16, self, lambda: self._restore_scroll(generation, attempt + 1))

        # A title or an outgoing document's loadFinished can arrive before the
        # requested chapter is ready. Confirm both the document and actual
        # renderer scroll before consuming its saved position.
        self._run_js(f"""
            (() => {{
                const expected = new URL({expected});
                const current = new URL(location.href);
                expected.hash = current.hash = '';
                if (current.href !== expected.href || document.readyState !== 'complete')
                    return false;
                const root = document.scrollingElement || document.documentElement;
                const target = Math.max(0, Math.min(root.scrollHeight * {fraction},
                                                   root.scrollHeight - innerHeight));
                window.scrollTo(0, target);
                return Math.abs(window.scrollY - target) <= 1;
            }})();
        """, restored)

    def _bump(self, factor: float) -> None:
        self._zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, self._zoom * factor))
        self._apply_zoom()

    def _apply_zoom(self) -> None:
        self._web.setZoomFactor(self._zoom)

    def _show_error(self, message):
        self._error.setText(message)
        self._error.show()

    def _chapter_url(self, i, fragment="", href=None):
        source = Path(self._book._inside(self._book.tmpdir, href) if href is not None
                      else self._book.chapters[i][1]).resolve()
        if str(source) not in self._chapter_paths and source.suffix.lower() not in (".xhtml", ".html", ".htm"):
            raise ValueError("This link does not point to a readable page in this EPUB.")
        target = self._html_paths.get(str(source))
        if target is None:
            # File-backed pages avoid setContent's encoded 2 MB limit, and a
            # sibling copy keeps relative images, CSS and fonts working.
            # Well-formed XHTML is shown as XML, byte for byte (its encoding
            # comes from its declaration); anything else as HTML.
            xhtml = _renders_as_xhtml(source)
            with source.open("rb") as reader, tempfile.NamedTemporaryFile(
                    dir=source.parent, prefix=".vocab-", suffix=".xhtml" if xhtml else ".html",
                    delete=False) as writer:
                if not xhtml:
                    prefix = reader.read(3)
                    # EPUB defaults to UTF-8; file URLs otherwise guess legacy
                    # encodings. Keep an existing UTF-8/UTF-16 byte-order mark.
                    if not prefix.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
                        writer.write(b"\xef\xbb\xbf")
                    writer.write(prefix)
                shutil.copyfileobj(reader, writer)
                target = writer.name
            self._html_paths[str(source)] = target
            self._source_paths[str(Path(target).resolve())] = str(source)
        url = QUrl.fromLocalFile(target)
        url.setFragment(fragment)
        return url

    def _set_chapter(self, i):
        self._idx = i
        self._picker.blockSignals(True)
        self._picker.setCurrentIndex(i)
        self._picker.blockSignals(False)
        self._prev.setEnabled(i > 0)
        self._next.setEnabled(i < len(self._book.chapters) - 1)

    def _load(self, i: int, fragment="", href=None):
        if not (0 <= i < len(self._book.chapters)):
            return
        try:
            url = self._chapter_url(i, fragment, href)
        except (OSError, ValueError) as error:
            self._pending_scroll = None
            self._pending_highlight = None
            self._set_chapter(self._idx)
            self._show_error(f"Could not read chapter: {error}")
            return
        self._set_chapter(i)
        self._position_known = True
        # The page Chromium last finished loading, even if another load was
        # requested since and has not appeared yet.
        same_document = Path(url.toLocalFile()).resolve() == self._loaded_path
        in_flight = not self._document_loaded
        self._current_path = self._source_paths[str(Path(url.toLocalFile()).resolve())]
        self._error.hide()
        self._requested_url = url
        self._load_generation += 1
        requested = self._pending_scroll if self._pending_scroll is not None else 0.0
        if same_document:
            # Chromium treats the page it shows as an in-page jump, with no
            # load events: restore the requested place directly. Loading it
            # also cancels a navigation still in flight.
            self._document_loaded = True
            if self._pending_scroll is None:
                self._scroll_fresh = True  # Qt's cached scroll is this document's
            else:
                self._requested_scroll = requested
                self._stale_scroll = self._web.page().scrollPosition().y()
                self._scroll_fresh = False
            if fragment or in_flight:
                self._web.load(url)
            generation = self._load_generation
            QTimer.singleShot(0, self, lambda: self._restore_location(generation))
            return
        # Until it loads, this chapter's place is its requested one, never the
        # outgoing chapter's scroll still cached by Qt.
        self._begin_document(requested)
        self._web.load(url)
        self._apply_zoom()

    def _navigate(self, request):
        if not request.isMainFrame():
            # Frames (iframe, object) may show pages from this book only, never
            # other local files or folder listings.
            url = request.url()
            if url.isLocalFile():
                try:
                    Path(url.toLocalFile()).resolve().relative_to(Path(self._book.tmpdir).resolve())
                except (OSError, ValueError):
                    request.reject()
            return
        url = request.url()
        path = Path(url.toLocalFile()).resolve() if url.isLocalFile() else None
        source = Path(self._source_paths.get(str(path), str(path))) if path else None
        try:
            href = quote_url(source.relative_to(Path(self._book.tmpdir).resolve()).as_posix(), safe="/") if source else None
        except ValueError:
            href = None
        if href is None:
            request.reject()
            self._show_error("This link points outside this EPUB.")
            return
        i = self._chapter_paths.get(str(source), self._idx)
        try:
            target = self._chapter_url(i, url.fragment(), href)
        except (OSError, ValueError) as error:
            request.reject()
            self._show_error(f"Could not read chapter: {error}")
            return
        if target != url:
            # Leave Chromium's navigation callback before starting another load.
            request.reject()
            self._pending_scroll = None
            self._set_highlight_source(None)
            QTimer.singleShot(0, self, lambda: self._load(i, url.fragment(), href))
            return
        if i != self._idx:
            self._pending_scroll = None
            self._set_chapter(i)
        if url != self._requested_url:
            self._load_generation += 1
            self._pending_scroll = None
            self._set_highlight_source(None)
            if not self._requested_url.isLocalFile() or (
                    Path(url.toLocalFile()).resolve() != Path(self._requested_url.toLocalFile()).resolve()):
                # Another document: report its top until it has loaded.
                self._begin_document(0.0)
            self._position_known = True
        self._current_path = str(source)
        self._error.hide()
        self._requested_url = url

    def _loading_changed(self, info):
        from PySide6.QtWebEngineCore import QWebEngineLoadingInfo
        if (not info.url().isLocalFile() or not self._requested_url.isLocalFile() or
                Path(info.url().toLocalFile()).resolve() != Path(self._requested_url.toLocalFile()).resolve()):
            return
        if info.status() == QWebEngineLoadingInfo.LoadStatus.LoadSucceededStatus:
            self._on_loaded()
        elif info.status() == QWebEngineLoadingInfo.LoadStatus.LoadFailedStatus:
            self._document_loaded = False
            self._loaded_path = None
            self._position_known = False  # keep the previously saved place
            self._web.hide()
            self._show_error(f"Could not read chapter: {info.errorString()}")
        elif info.status() == QWebEngineLoadingInfo.LoadStatus.LoadStartedStatus:
            self._document_loaded = False

    def _go(self, delta: int):
        self._pending_scroll = None
        self._set_highlight_source(None)
        self._load(self._idx + delta)

    def _jump(self, i: int):
        if not 0 <= i < len(self._book.chapters):
            return
        if i != self._idx or self._current_path != str(Path(self._book.chapters[i][1]).resolve()):
            self._pending_scroll = None
            self._set_highlight_source(None)
            self._load(i)

    def _open_ext(self):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.path))

    def set_back_label(self, text):
        self._back.setText(text)

    def source_location(self):
        page = self._web.page()
        height = max(1.0, page.contentsSize().height())
        if self._document_loaded and self._pending_scroll is not None:
            fraction = self._pending_scroll
        elif not (self._document_loaded and self._scroll_fresh):
            fraction = self._requested_scroll  # Qt may still cache the old document's scroll
        else:
            fraction = max(0.0, min(1.0, page.scrollPosition().y() / height))
        source = {"kind": "epub", "filename": self._fname, "chapter": self._idx,
                  "scroll": fraction}
        # Footnotes and tables of contents can be outside the reading spine.
        # Keep their actual file so captures and reopening return to that page.
        if self._current_path and self._current_path not in self._chapter_paths:
            source["href"] = quote_url(Path(self._current_path).relative_to(self._book.tmpdir).as_posix(), safe="/")
        return source

    def _capture(self):
        if not self.ctx or not self._document_loaded:
            return
        generation = self._load_generation
        source = self.source_location()

        def selected(result):
            if not isValid(self) or generation != self._load_generation or not self._document_loaded:
                return
            try:
                anchor = json.loads(result)
                quote = anchor.pop("quote").strip()
                source["scroll"] = anchor.pop("scroll")
            except (TypeError, ValueError, KeyError):
                self.ctx.log.emit("Highlight a passage first, then click Capture.")
                return
            source["anchor"] = anchor
            self.ctx.capture_requested.emit(self._module or "", quote, source)

        # Text and DOM offsets must come from the same renderer snapshot.
        self._run_js(SELECTION_ANCHOR_JS, selected)

    def go_to(self, source):
        self._pending_scroll = max(0.0, min(1.0, float(source.get("scroll", 0))))
        self._set_highlight_source(source)
        self._load(max(0, min(int(source.get("chapter", 0)), len(self._book.chapters) - 1)),
                   href=source.get("href"))

    # JS: the selected word and the sentence around that occurrence (not the
    # first match of the word), taken from the text before and after the
    # selection inside its block, so inline markup does not split it and the
    # page title never joins it.
    _SEL_JS = r"""
    (function () {
      var sel = window.getSelection();
      if (!sel || sel.rangeCount === 0) return "";
      var range = sel.getRangeAt(0);
      var word = range.toString().trim();
      if (!word) return "";
      var body = document.body;
      var start = range.startContainer;
      var element = start.nodeType === 1 ? start : start.parentElement;
      if (!body || !element || !body.contains(element))
        return JSON.stringify({word: word, sentence: word});
      var block = element;
      while (block !== body && /^(inline|contents)/.test(getComputedStyle(block).display))
        block = block.parentElement;
      var before = document.createRange();
      before.setStart(block, 0);
      before.setEnd(range.startContainer, range.startOffset);
      var after = document.createRange();
      after.setStart(range.endContainer, range.endOffset);
      after.setEnd(block, block.childNodes.length);
      var head = before.toString(), tail = after.toString();
      var ends = /[.!?]+["'’”)\]]*\s/g, cut = 0, match;
      while ((match = ends.exec(head)) !== null) cut = ends.lastIndex;
      head = head.slice(cut).slice(-400);
      match = /^[\s\S]*?[.!?]+["'’”)\]]*(?=\s|$)/.exec(tail);
      tail = (match ? match[0] : tail).slice(0, 400);
      var sentence = (head + range.toString() + tail).replace(/\s+/g, " ").trim();
      return JSON.stringify({word: word, sentence: sentence});
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
            self._run_js(self._SEL_JS, _done)
        except Exception:
            pass

    def save_position(self) -> None:
        """Read Qt's cached scroll position synchronously, including on close."""
        if not (self._storage and self._module) or not self._position_known:
            return
        source = self.source_location()
        pos = {"chapter": self._idx, "scroll": source["scroll"], "zoom": self._zoom}
        if "href" in source:
            pos["href"] = source["href"]
        self._storage.save_reading_pos(
            self._module, self._fname, pos)

    def refresh(self) -> None:
        pass

    def focus_default(self) -> None:
        self._web.setFocus()

    def closeEvent(self, e):
        _save_on_close(self)
        super().closeEvent(e)

    def on_replaced(self) -> None:
        """Called by Pane.set_component when this viewer is swapped out. If the
        position cannot be saved this raises, and the viewer stays usable."""
        self.save_position()
        self.release()

    def release(self) -> None:
        # A renderer reply can arrive between pane replacement and deleteLater.
        self._load_generation += 1
        self._document_loaded = False


def _save_on_close(viewer):
    try:
        viewer.save_position()
    except Exception as error:
        if viewer.ctx:
            viewer.ctx.log.emit(f"! Could not save your place in {viewer._fname}: {error}")


# --------------------------------------------------------------------------- #
def make_viewer(path: str, ctx=None, module=None, source=None):
    """Return the right viewer for a file, or None if unsupported."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        viewer = PdfViewer(path, ctx, module=module)
        if source is not None:
            viewer.go_to(source)
        return viewer
    if ext == ".epub":
        return EpubViewer(path, ctx, module=module, source=source)
    return None
