"""Verify in-context sense disambiguation + highlight-to-add wiring."""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from vocab.core.models import Sense
from vocab.study.disambiguate import get_disambiguator, NlpDisambiguator, OllamaDisambiguator


def main() -> int:
    # --- NLP disambiguator: POS filter fixes the 'ephemeral' mayfly wart ---
    dis = get_disambiguator("nlp")
    assert isinstance(dis, NlpDisambiguator)

    # senses as WordNet returns them (mayfly noun first, adjective second)
    ephemeral = [
        Sense(definition="anything short-lived, as an insect that lives only a day", pos="noun"),
        Sense(definition="lasting a very short time", pos="adjective"),
    ]
    ranked = dis.rank("ephemeral", "the ephemeral joys of childhood", ephemeral)
    assert ranked[0].pos == "adjective", f"POS filter failed: {ranked[0].pos}"
    assert "short time" in ranked[0].definition
    print("[ok] NLP: 'ephemeral' in context -> adjective 'lasting a very short time' (mayfly demoted)")

    # --- Lesk picks among same-POS senses (bank: money vs river) ---
    bank = [
        Sense(definition="sloping land beside a body of water", pos="noun", example="he sat on the river bank"),
        Sense(definition="a financial institution that accepts deposits", pos="noun", example="he deposited money at the bank"),
    ]
    ranked = dis.rank("bank", "he deposited the check at the bank downtown", bank)
    assert "financial" in ranked[0].definition or "deposits" in ranked[0].definition, \
        f"Lesk did not pick the money sense: {ranked[0].definition}"
    print("[ok] NLP: 'bank' + 'deposited the check' -> financial-institution sense")

    ranked2 = dis.rank("bank", "she sat on the river bank watching the water", bank)
    assert "water" in ranked2[0].definition or "sloping" in ranked2[0].definition, \
        f"Lesk did not pick the river sense: {ranked2[0].definition}"
    print("[ok] NLP: 'bank' + 'river ... water' -> river-bank sense")

    # no sentence / single sense -> unchanged (safe no-op)
    assert dis.rank("x", "", ephemeral) == ephemeral
    assert dis.rank("x", "some sentence", ephemeral[:1]) == ephemeral[:1]
    print("[ok] NLP: no-op when no sentence or single sense")

    # --- Ollama backend degrades to NLP when server is down (no cost) ---
    oll = OllamaDisambiguator(fallback=NlpDisambiguator())
    ranked = oll.rank("ephemeral", "the ephemeral joys of childhood", ephemeral)
    assert ranked[0].pos == "adjective", "Ollama fallback to NLP failed"
    print("[ok] Ollama backend falls back to NLP when server down (zero cost)")

    # --- cmd_add threads the sentence through to disambiguation ---
    from vocab.cli.app import App
    app = App(root=tempfile.mkdtemp(prefix="vocab_dis_"))
    if not app.effective_dictionary()[0].available():
        print("[skip] WordNet corpus not installed; cmd_add context test skipped")
    else:
        app.cmd_create("Reading"); app.cmd_use("Reading")
        msg = app.cmd_add("ephemeral", sentence="the ephemeral joys of childhood")
        wl = app.storage.load_words("Reading")
        w = wl.words[wl.normalize_key("ephemeral")]
        assert "short" in w.senses[0].definition.lower(), \
            f"cmd_add did not use context; primary sense = {w.senses[0].definition!r}"
        print(f"[ok] cmd_add(sentence=...) stored context sense: {w.senses[0].definition!r}")

    # --- highlight-to-add signal path (MainWindow handler) ---
    from PySide6.QtWidgets import QApplication
    from vocab.gui.main_window import MainWindow
    app_qt = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow(root=tempfile.mkdtemp(prefix="vocab_hl_"))
    win.app.cmd_create("Book"); win.app.cmd_use("Book")
    n0 = len(win.app.storage.load_words("Book").words)
    win.ctx.add_word_requested.emit("ephemeral", "the ephemeral joys of childhood")
    app_qt.processEvents()
    n1 = len(win.app.storage.load_words("Book").words)
    if win.app.effective_dictionary()[0].available():
        assert n1 == n0 + 1, "highlight-to-add did not add the word"
        print("[ok] add_word_requested -> word added to current module")
    else:
        print("[skip] WordNet not installed; highlight-add count check skipped")

    # --- EPUB selection JS extracts word + containing sentence (live page) ---
    import zipfile
    from PySide6.QtCore import QTimer
    from vocab.gui.viewers import EpubViewer
    tmpd = tempfile.mkdtemp(prefix="vocab_sel_")
    epath = os.path.join(tmpd, "s.epub")
    with zipfile.ZipFile(epath, "w") as z:
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                   '<rootfiles><rootfile full-path="c.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("c.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   'version="3.0" unique-identifier="id"><metadata '
                   'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>S</dc:title></metadata>'
                   '<manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>'
                   '</manifest><spine><itemref idref="c1"/></spine></package>')
        z.writestr("c1.xhtml",
                   "<html><body><p>The dog barked loudly. She felt an ephemeral joy "
                   "that morning. Then it faded.</p></body></html>")

    ev = EpubViewer(epath); ev.resize(600, 400); ev.show()
    got = {}
    def run_sel():
        # select the word "ephemeral" programmatically, then run the extractor JS
        js_select = """
        (function(){
          var p=document.querySelector('p'); var t=p.firstChild;
          var i=p.textContent.indexOf('ephemeral');
          var r=document.createRange(); r.setStart(t,i); r.setEnd(t,i+9);
          var s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
        })();
        """
        def after_select(_):
            ev._web.page().runJavaScript(ev._SEL_JS, capture)
        def capture(payload):
            got["payload"] = payload
            app_qt.quit()
        ev._web.page().runJavaScript(js_select, after_select)
    for _ in range(10):
        app_qt.processEvents()
    QTimer.singleShot(1500, run_sel)
    QTimer.singleShot(4000, app_qt.quit)
    app_qt.exec()

    import json as _json
    payload = got.get("payload")
    assert payload, "selection JS returned nothing"
    data = _json.loads(payload)
    assert data["word"] == "ephemeral", f"wrong word: {data}"
    assert "ephemeral joy" in data["sentence"], f"sentence not extracted: {data}"
    assert "dog barked" not in data["sentence"], f"grabbed wrong sentence: {data}"
    print(f"[ok] EPUB selection JS extracted word+sentence: {data}")

    print("\nDISAMBIGUATION + HIGHLIGHT-ADD PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
