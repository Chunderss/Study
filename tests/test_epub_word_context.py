"""Add word sends the sentence around the selected occurrence."""
import pytest
pytest.importorskip("PySide6")

from .test_epub_position_lifecycle import run_reader


def test_the_sentence_comes_from_the_selected_occurrence(tmp_path):
    run_reader(tmp_path, r'''
import json
body = ('<p id="banks">First bank deals money. The river bank supports trees.</p>'
        '<p id="art">Let us start. The art of reading.</p>'
        '<p id="em">A <em>quick</em> fox jumps. Another sentence.</p>'
        '<h2 id="heading">No full stop here</h2>'
        '<p id="tiny">Tiny bank.</p>'
        '<p id="ruby">Before it. The <ruby>bank<rt>gloss</rt></ruby> is near the river. Next one.</p>'
        '<p id="mono">\u6628\u65e5\u3001<ruby>\u9280<rt>\u304e\u3093</rt></ruby>'
        '<ruby>\u884c<rt>\u3053\u3046</rt></ruby>\u306b\u884c\u3063\u305f\u3002 Next.</p>')
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  '<item id="a" href="a.xhtml"/></manifest><spine><itemref idref="a"/></spine></package>')
    book.writestr("a.xhtml", page.format("Main", body))

viewer = open_viewer()
wait_for(lambda: settled(viewer, "Main"), viewer)

def context(element, word, nth=0, backwards=False):
    js(viewer, """(() => {
        const words = [];
        const walker = document.createTreeWalker(document.getElementById(%s), NodeFilter.SHOW_TEXT);
        for (let node; (node = walker.nextNode());) {
            const pattern = new RegExp("\\\\b" + %s + "\\\\b", "g");
            for (let m; (m = pattern.exec(node.data));) words.push([node, m.index]);
        }
        const [node, offset] = words[%d], end = offset + %s.length;
        const selection = getSelection();
        if (%s) selection.setBaseAndExtent(node, end, node, offset);
        else selection.setBaseAndExtent(node, offset, node, end);
        return true;
    })()""" % (json.dumps(element), json.dumps(word), nth, json.dumps(word),
               "true" if backwards else "false"))
    return json.loads(js(viewer, viewer._SEL_JS))

assert context("banks", "bank", 1) == {"word": "bank", "sentence": "The river bank supports trees."}
assert context("banks", "bank", 1, backwards=True)["sentence"] == "The river bank supports trees."
assert context("banks", "bank", 0)["sentence"] == "First bank deals money."
assert context("art", "art")["sentence"] == "The art of reading."
assert context("em", "fox")["sentence"] == "A quick fox jumps."
assert context("em", "quick")["sentence"] == "A quick fox jumps."
assert context("tiny", "bank")["sentence"] == "Tiny bank."  # not the heading, not the title
assert context("ruby", "bank")["sentence"] == "The bank is near the river."
# A selection across mono ruby (two bases with their glosses) keeps neither gloss.
js(viewer, """(() => {
    const bases = document.querySelectorAll("#mono ruby");
    getSelection().setBaseAndExtent(bases[0].firstChild, 0, bases[1].firstChild, 1);
    return true;
})()""")
mono = json.loads(js(viewer, viewer._SEL_JS))
assert mono["word"] == "\u9280\u884c", mono
assert "\u304e" not in mono["sentence"] and mono["sentence"].startswith("\u6628\u65e5\u3001\u9280\u884c"), mono
''')
