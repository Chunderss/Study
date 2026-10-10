"""A capture whose passage is gone still opens at its saved scroll."""
import pytest
pytest.importorskip("PySide6")

from .test_epub_position_lifecycle import run_reader


@pytest.mark.parametrize("anchor", ["missing_anchor", "missing_quote"])
def test_a_missing_passage_falls_back_to_the_saved_scroll(tmp_path, anchor):
    run_reader(tmp_path, r'''
from vocab.core.learning import LearningStore
source = {"kind": "epub", "filename": "book.epub", "chapter": 1, "scroll": 0.6}
if "%s" == "missing_anchor":
    source["anchor"] = {"version": 1, "exact": "A passage that was edited away", "prefix": "", "suffix": ""}
item = LearningStore(data.storage).save_capture(
    "Book", kind="concept", prompt="Why?", quote="A passage that was edited away", source=source)
target = dict(source, capture_id=item["id"])

def fraction(viewer):
    return js(viewer, "window.scrollY / (document.scrollingElement.scrollHeight)")

viewer = open_viewer()
wait_for(lambda: settled(viewer, "A"), viewer)
viewer.go_to(target)
wait_for(lambda: settled(viewer, "B") and viewer._pending_highlight is None, viewer)
assert abs(fraction(viewer) - 0.6) < 0.01, fraction(viewer)
assert abs(viewer.source_location()["scroll"] - 0.6) < 0.01

# The reader's own scrolling afterwards is never undone.
js(viewer, "window.scrollTo(0, 0); true;")
QTest.qWait(300)
assert fraction(viewer) == 0

# Opened hidden and shown later, the fallback still applies.
hidden = EpubViewer(str(path), ctx, source=target)
viewers.append(hidden)
hidden.resize(900, 600)
wait_for(lambda: hidden._document_loaded, hidden)
hidden.show()
wait_for(lambda: settled(hidden, "B") and hidden._pending_highlight is None, hidden)
assert abs(fraction(hidden) - 0.6) < 0.01, fraction(hidden)
''' % anchor)
