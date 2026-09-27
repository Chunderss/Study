"""A delayed renderer reply must retain its source or be discarded."""
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QWidget
from shiboken6 import delete, isValid

from .test_markdown_preview import qt
from vocab.gui.viewers import EpubViewer


@pytest.mark.parametrize("change", ["module", "navigation", "replacement", "failed_save", "deletion"])
def test_capture_callback_keeps_original_location_and_ignores_stale_reader(qt, change):
    viewer = EpubViewer.__new__(EpubViewer)
    QWidget.__init__(viewer)
    captures, callbacks = [], []
    viewer.ctx = SimpleNamespace(capture_requested=SimpleNamespace(emit=lambda *args: captures.append(args)))
    viewer._module = "Original"
    viewer._load_generation = 4
    viewer._document_loaded = True
    viewer._web = SimpleNamespace(page=lambda: SimpleNamespace(
        runJavaScript=lambda script, callback: callbacks.append(callback)))
    viewer.source_location = lambda: {"kind": "epub", "filename": "book.epub", "chapter": 2, "scroll": 0.1}
    viewer.save_position = lambda: None
    anchor = {"version": 1, "exact": "First.Second.", "prefix": "Before", "suffix": "After",
              "start": 6, "end": 19}
    try:
        viewer._capture()
        assert len(callbacks) == 1
        if change == "navigation":
            viewer._load_generation += 1
        elif change == "replacement":
            viewer.on_replaced()
        elif change == "failed_save":
            def cannot_save():
                raise OSError("Unavailable storage")
            viewer.save_position = cannot_save
            with pytest.raises(OSError):
                viewer.on_replaced()
        elif change == "deletion":
            delete(viewer)
        else:
            viewer.ctx.app = SimpleNamespace(current_module="Other")
        callbacks[0](json.dumps(dict(anchor, quote="First.\n\nSecond.", scroll=0.72)))
        if change == "module":
            assert captures == [("Original", "First.\n\nSecond.", {
                "kind": "epub", "filename": "book.epub", "chapter": 2,
                "scroll": 0.72, "anchor": anchor})]
        else:
            assert captures == []
    finally:
        if isValid(viewer):
            delete(viewer)
