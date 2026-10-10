"""Verify in-context sense disambiguation + highlight-to-add wiring."""
import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Import this checkout even when another copy of vocab is installed.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vocab import nltk_resources
from vocab.core.models import Sense
from vocab.study.disambiguate import get_disambiguator, NlpDisambiguator, OllamaDisambiguator


def main() -> int:
    # --- NLP disambiguator: POS filter fixes the 'ephemeral' mayfly wart ---
    dis = get_disambiguator("nlp")
    assert isinstance(dis, NlpDisambiguator)
    # The part-of-speech checks need NLTK's tagger; the others run without it.
    tagger_problem = nltk_resources.pos_tagger_problem()
    if tagger_problem:
        print(f"[skip] part-of-speech checks: {tagger_problem.reason}")

    # senses as WordNet returns them (mayfly noun first, adjective second)
    ephemeral = [
        Sense(definition="anything short-lived, as an insect that lives only a day", pos="noun"),
        Sense(definition="lasting a very short time", pos="adjective"),
    ]
    if not tagger_problem:
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
    if not tagger_problem:
        oll = OllamaDisambiguator(fallback=NlpDisambiguator())
        # Report the server as down, so a local Ollama cannot answer instead.
        oll._server_up = lambda use_cache=True: False
        ranked = oll.rank("ephemeral", "the ephemeral joys of childhood", ephemeral)
        assert ranked[0].pos == "adjective", "Ollama fallback to NLP failed"
        print("[ok] Ollama backend falls back to NLP when server down (zero cost)")

    # --- cmd_add threads the sentence through to disambiguation ---
    from vocab.cli.app import App
    app = App(root=tempfile.mkdtemp(prefix="vocab_dis_"))
    # Without WordNet the app falls back to an online dictionary: skip, don't go online.
    wordnet = app.effective_dictionary()[0].id == "wordnet"
    if not wordnet:
        print("[skip] WordNet is not installed; cmd_add and highlight-to-add checks skipped")
    elif tagger_problem:
        print("[skip] cmd_add context check: needs the part-of-speech tagger")
    else:
        app.cmd_create("Reading"); app.cmd_use("Reading")
        msg = app.cmd_add("ephemeral", sentence="the ephemeral joys of childhood")
        wl = app.storage.load_words("Reading")
        w = wl.words[wl.normalize_key("ephemeral")]
        # Both WordNet senses mention "short"; only the adjective fits the sentence.
        assert w.senses[0].pos == "adjective", \
            f"cmd_add did not use context; primary sense = {w.senses[0].definition!r}"
        print(f"[ok] cmd_add(sentence=...) stored context sense: {w.senses[0].definition!r}")

    # --- highlight-to-add signal path (MainWindow handler) ---
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    from vocab.gui.main_window import MainWindow
    app_qt = QApplication.instance() or QApplication(sys.argv)

    def wait_until(check, what, seconds=30):
        deadline = time.monotonic() + seconds
        while not check():
            assert time.monotonic() < deadline, f"timed out waiting for {what}"
            QTest.qWait(10)

    win = MainWindow(root=tempfile.mkdtemp(prefix="vocab_hl_"))
    win.app.cmd_create("Book"); win.app.cmd_create("Other"); win.app.cmd_use("Other")
    if wordnet:
        def counts():
            return {m: len(win.app.storage.load_words(m).words) for m in ("Book", "Other")}
        before = counts()
        finished = []
        win.ctx.word_added.connect(lambda token, ok: finished.append(ok))
        # word, sentence, and the module of the reader it was highlighted in,
        # which is not the current module here
        win.ctx.add_word_requested.emit("ephemeral", "the ephemeral joys of childhood", "Book")
        # The lookup runs on a worker thread; the result is saved on this one.
        wait_until(lambda: finished, "the highlight-to-add lookup")
        assert finished == [True], "highlight-to-add lookup failed"
        assert counts() == {"Book": before["Book"] + 1, "Other": before["Other"]}, \
            "highlight-to-add did not add the word to the reader's module"
        print("[ok] add_word_requested -> word added to the reader's module after the lookup")
        if tagger_problem:
            print("[skip] highlight-to-add sense order: needs the part-of-speech tagger")
        else:
            words = win.app.storage.load_words("Book")
            w = words.words[words.normalize_key("ephemeral")]
            assert w.senses[0].pos == "adjective", \
                f"highlight-to-add ignored the sentence: {w.senses[0].definition!r}"
            print(f"[ok] add_word_requested stored the in-context sense: {w.senses[0].definition!r}")

    # --- EPUB selection JS extracts word + containing sentence (live page) ---
    import zipfile
    from PySide6.QtCore import QEvent
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
    wait_until(lambda: ev._document_loaded and not ev._web.page().isLoading(),
               "the EPUB chapter to load")
    # select the word "ephemeral" programmatically, then run the extractor JS
    js_select = """
    (function(){
      var p=document.querySelector('p'); var t=p.firstChild;
      var i=p.textContent.indexOf('ephemeral');
      var r=document.createRange(); r.setStart(t,i); r.setEnd(t,i+9);
      var s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
    })();
    """
    selected, got = [], []
    ev._run_js(js_select, selected.append)
    wait_until(lambda: selected, "the selection script")
    ev._run_js(ev._SEL_JS, got.append)
    wait_until(lambda: got, "the selection payload")

    import json as _json
    payload = got[0]
    assert payload, "selection JS returned nothing"
    data = _json.loads(payload)
    assert data["word"] == "ephemeral", f"wrong word: {data}"
    assert "ephemeral joy" in data["sentence"], f"sentence not extracted: {data}"
    assert "dog barked" not in data["sentence"], f"grabbed wrong sentence: {data}"
    print(f"[ok] EPUB selection JS extracted word+sentence: {data}")

    # Close the reader before QApplication goes: a WebEngine page that
    # outlives its profile crashes the interpreter at exit.
    for widget in (ev, win):
        widget.close()
        widget.deleteLater()
    app_qt.sendPostedEvents(None, QEvent.DeferredDelete)
    app_qt.processEvents()

    print("\nDISAMBIGUATION + HIGHLIGHT-ADD PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
