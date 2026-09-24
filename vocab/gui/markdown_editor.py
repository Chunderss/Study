"""Plain Markdown source plus a debounced, local Qt preview (no browser/LLM)."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (QLabel, QSplitter, QTextBrowser, QTextEdit,
                               QVBoxLayout, QWidget)


class MarkdownPreview(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MarkdownPreview")
        self.setAccessibleName("Rendered Markdown preview")
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def loadResource(self, resource_type, url):
        # A note preview must not fetch remote resources or arbitrary local files.
        return None


class MarkdownEditor(QWidget):
    """Rendering never rewrites the source document or moves its editing cursor."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter)
        self.editor = QTextEdit(acceptRichText=False)
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

    def render(self):
        self._timer.stop()
        bar = self.preview.verticalScrollBar()
        old_scroll = bar.value()
        self.preview.document().setMarkdown(
            self.editor.toPlainText(),
            QTextDocument.MarkdownFeature.MarkdownDialectGitHub
            | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        bar.setValue(old_scroll)
