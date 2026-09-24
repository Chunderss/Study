"""Prove QtWebEngine actually loaded the EPUB chapter content by reading the LIVE
DOM via JavaScript (independent of screenshot rasterization, which is unreliable
for QtWebEngine offscreen)."""
import os, sys, tempfile, zipfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from vocab.gui import theme
from vocab.gui.viewers import EpubViewer

def make_epub(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title></metadata>'
                   '<manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("OEBPS/ch1.xhtml",
                   "<html><body><h1>Chapter One</h1><p>UNIQUE_MARKER_TEXT_12345 the "
                   "father's advice.</p></body></html>")

app = QApplication(sys.argv)
app.setStyleSheet(theme.stylesheet())
tmp = tempfile.mkdtemp(prefix="vocab_epubdom_")
epath = os.path.join(tmp, "t.epub"); make_epub(epath)
v = EpubViewer(epath); v.resize(700, 500); v.show()

result = {}
def check():
    def got(text):
        result["body"] = text or ""
        app.quit()
    v._web.page().runJavaScript("document.body ? document.body.innerText : ''", got)

for _ in range(10):
    app.processEvents()
QTimer.singleShot(2000, check)
QTimer.singleShot(4000, app.quit)  # safety
app.exec()

body = result.get("body", "")
print("DOM innerText repr:", repr(body[:200]))
assert "UNIQUE_MARKER_TEXT_12345" in body, "EPUB chapter text NOT present in live DOM!"
assert "Chapter One" in body, "EPUB heading not in live DOM!"
assert "does not appear to have any style" not in body, \
    "EPUB rendered as raw XML source, not HTML!"
print("[ok] QtWebEngine rendered the EPUB chapter as HTML (not raw XML source)")
print("VIEWER DOM CHECK PASSED")
