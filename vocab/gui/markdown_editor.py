"""Plain Markdown source plus a debounced, local Qt preview (no browser/LLM)."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import (QLabel, QSplitter, QTextBrowser, QPlainTextEdit,
                               QVBoxLayout, QWidget)


class MarkdownPreview(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MarkdownPreview")
        self.setAccessibleName("Rendered Markdown preview")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    _placeholder = None

    def loadResource(self, resource_type, url):
        # A note preview must not fetch remote resources, arbitrary local files
        # or UNC paths (on Windows those contact the named server). Returning
        # nothing would let Qt load the file itself, so images get a blank
        # placeholder, which Qt caches instead of asking again on each repaint.
        if resource_type == QTextDocument.ResourceType.ImageResource:
            if MarkdownPreview._placeholder is None:
                image = QImage(1, 1, QImage.Format_ARGB32)
                image.fill(Qt.transparent)
                MarkdownPreview._placeholder = image
            return MarkdownPreview._placeholder
        return ""


class MarkdownEditor(QWidget):
    """Rendering never rewrites the source document or moves its editing cursor."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)
        self.editor = QPlainTextEdit()
        self.editor.setAccessibleName("Markdown source")
        self.editor.setTabChangesFocus(True)
        self.preview = MarkdownPreview()
        self._source_panel = self._panel("Markdown", self.editor)
        self._preview_panel = self._panel("Live preview", self.preview)
        self.splitter.addWidget(self._source_panel)
        self.splitter.addWidget(self._preview_panel)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.render)
        self.editor.textChanged.connect(self._timer.start)

    @staticmethod
    def _panel(title, content):
        panel = QWidget()
        panel.setMinimumWidth(0)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel(title, objectName="Muted"))
        layout.addWidget(content, 1)
        return panel

    def toggle_preview(self):
        show = self._preview_panel.isHidden()
        if not show and self.preview.hasFocus():
            self.editor.setFocus()
        self._preview_panel.setVisible(show)
        if show:
            self.render()

    def resizeEvent(self, event):
        orientation = Qt.Horizontal if self.width() >= 600 else Qt.Vertical
        if self.splitter.orientation() != orientation:
            self.splitter.setOrientation(orientation)
            extent = self.width() if orientation == Qt.Horizontal else self.height()
            self.splitter.setSizes([extent // 2, extent // 2])
        super().resizeEvent(event)

    def render(self, reset_scroll=False):
        self._timer.stop()
        bar = self.preview.verticalScrollBar()
        old_scroll = 0 if reset_scroll else bar.value()
        horizontal = self.preview.horizontalScrollBar()
        old_horizontal = 0 if reset_scroll else horizontal.value()
        self.preview.document().setMarkdown(
            self.editor.toPlainText(),
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        bar.setValue(old_scroll)
        horizontal.setValue(old_horizontal)
