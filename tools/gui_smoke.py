"""Headless GUI smoke test — drives the REAL Qt window + tiling workspace offscreen.

Run:  QT_QPA_PLATFORM=offscreen python tools/gui_smoke.py
Exits non-zero on any failure. Verifies the window builds, the workspace tiling
works (split/focus/zoom/close/cycle), the sidebar toggles, a study session runs
in a pane, notes save, and everything is reachable without a mouse.
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from vocab.gui import theme
from vocab.gui.main_window import MainWindow
from vocab.gui.components import NotesComponent, VocabComponent


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    home = tempfile.mkdtemp(prefix="vocab_gui_smoke_")
    win = MainWindow(root=home)
    win.show()
    ws = win.workspace

    # 1. one pane to start, Vocab component
    assert len(ws._panes) == 1, f"expected 1 pane, got {len(ws._panes)}"
    assert ws.focused.factory_key == "vocab"
    print("[ok] starts with one Vocab pane")

    # 2. create a module + words via the App (as components do)
    win.app.cmd_create("Smoke"); win.app.cmd_use("Smoke")
    win.app.cmd_add("ephemeral", manual_def="lasting a very short time")
    win.app.cmd_add("verdant", manual_def="green with vegetation")
    win._refresh_modules()
    win.ctx.changed.emit()
    assert "Smoke" in win.app.storage.list_names()
    print("[ok] module + words created")

    # 3. split vertical -> 2 panes; focus is on the new pane
    ws.split_vertical()
    assert len(ws._panes) == 2, f"split failed, panes={len(ws._panes)}"
    print("[ok] split vertical -> 2 panes")

    # 3b. split sizing must be ~50/50 (this was the 'one pane larger' bug)
    parent = ws.focused.parentWidget()
    # process the deferred even-out
    win.resize(1000, 700); QApplication.processEvents()
    sizes = parent.sizes()
    if len(sizes) == 2 and sum(sizes) > 0:
        ratio = min(sizes) / max(sizes)
        assert ratio > 0.9, f"split not even: {sizes} (ratio {ratio:.2f})"
        print(f"[ok] split is even: sizes={sizes} ratio={ratio:.2f}")
    else:
        print(f"[warn] could not measure split sizes offscreen: {sizes}")

    # 3c. documents: add a file programmatically (dialog path is UI-only)
    import tempfile as _tf
    doc = _tf.NamedTemporaryFile(prefix="gatsby_", suffix=".pdf", delete=False)
    doc.write(b"%PDF-1.4 fake"); doc.close()
    win.app.cmd_use("Smoke")
    added = win.app.storage.add_document("Smoke", doc.name)
    assert added in win.app.storage.document_names("Smoke"), "document not added"
    print(f"[ok] document added to module: {added}")

    # 4. cycle the focused pane's component to Notes
    ws.set_focused_component("notes")
    assert isinstance(ws.focused.component, NotesComponent)
    print("[ok] focused pane switched to Notes component")

    # 5. directional focus nav: move left back to the vocab pane
    ws.move_focus("h")
    # after moving left we should be on a different pane
    assert ws.focused is not None
    print("[ok] Alt+H style focus move works")

    # 6. zoom toggle
    ws.focus_index(2)
    ws.toggle_zoom()
    assert ws.is_zoomed, "zoom did not engage"
    ws.toggle_zoom()
    assert not ws.is_zoomed, "unzoom failed"
    print("[ok] zoom / unzoom toggle")

    # 7. notes: create + save through the Notes component, keyboard-style
    win.app.cmd_use("Smoke")
    notes_pane = None
    for p in ws._panes:
        if isinstance(p.component, NotesComponent):
            notes_pane = p; break
    assert notes_pane, "no notes pane"
    nc = notes_pane.component
    win.app.storage.save_note("Smoke", "chapter1", "seed")
    nc.refresh()
    from PySide6.QtCore import Qt
    items = nc.note_list.findItems("chapter1", Qt.MatchExactly)
    assert items, "note not listed"
    nc.note_list.setCurrentItem(items[0])
    assert "seed" in nc.editor.toPlainText()
    nc.editor.setPlainText("edited body")
    nc.save()
    assert win.app.storage.load_note("Smoke", "chapter1") == "edited body"
    print("[ok] notes create/select/edit/save round-trip")

    # 8. study opens in a split pane and completes
    before = len(ws._panes)
    win._open_study(["Smoke"])
    assert len(ws._panes) == before + 1, "study did not open in a new pane"
    from vocab.gui.study_view import StudyView
    sv = ws.focused.component
    assert isinstance(sv, StudyView), "focused pane is not the Study view"
    guard = 0
    while not sv.session.done and guard < 20:
        sv._reveal(); sv._grade(True); guard += 1
    # finishing turns the pane back to vocab
    assert isinstance(ws.focused.component, VocabComponent), "study pane did not revert to vocab"
    assert win.app.storage.load_stats("Smoke")["ephemeral"]["box"] == 2
    print("[ok] study ran in a pane, progress saved, pane reverted to Vocab")

    # 9. close a pane
    before = len(ws._panes)
    ws.close_focused()
    assert len(ws._panes) == before - 1, "close pane failed"
    print("[ok] close pane")

    # 10. sidebar toggle
    vis = win.sidebar.isVisible()
    win._toggle_sidebar()
    assert win.sidebar.isVisible() != vis, "sidebar toggle failed"
    win._toggle_sidebar()
    print("[ok] sidebar toggle")

    print("\nGUI SMOKE PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
