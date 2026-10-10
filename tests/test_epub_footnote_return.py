"""A footnote outside the reading order never traps the reader."""
import pytest
pytest.importorskip("PySide6")

from .test_epub_position_lifecycle import run_reader


def test_choosing_the_current_chapter_leaves_a_footnote(tmp_path):
    run_reader(tmp_path, r'''
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.xhtml", page.format("Main", '<a id="note" href="notes.xhtml">Note</a>'))
    book.writestr("notes.xhtml", page.format("Note", "<p>Footnote without a return link</p>"))

viewer = open_viewer()
wait_for(lambda: settled(viewer, "Main"), viewer)
assert not viewer._prev.isEnabled() and not viewer._next.isEnabled()
js(viewer, 'document.getElementById("note").click(); true;')
wait_for(lambda: settled(viewer, "Note"), viewer)
assert viewer._picker.currentIndex() == 0
viewer._picker.activated.emit(0)  # the user chooses "1. a" again
wait_for(lambda: settled(viewer, "Main"), viewer)

# Reopened on the footnote, the same choice still returns to the chapter.
js(viewer, 'document.getElementById("note").click(); true;')
wait_for(lambda: settled(viewer, "Note"), viewer)
viewer.on_replaced()
assert "href" in saved()
reopened = open_viewer()
wait_for(lambda: settled(reopened, "Note"), reopened)
reopened._picker.activated.emit(0)
wait_for(lambda: settled(reopened, "Main"), reopened)
reopened.save_position()
assert "href" not in saved(), saved()
''')
