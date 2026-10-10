"""XHTML chapters keep XML semantics; other pages keep HTML's tolerance."""
import pytest
pytest.importorskip("PySide6")

from .test_epub_position_lifecycle import run_reader

NS = 'xmlns="http://www.w3.org/1999/xhtml"'


def test_xhtml_chapters_render_with_xml_semantics(tmp_path):
    run_reader(tmp_path, r'''
NS = 'xmlns="http://www.w3.org/1999/xhtml"'
chapters = {
    "title.xhtml": f'<html {NS}><head><title/></head><body><p id="text">Visible chapter text</p></body></html>',
    "script.xhtml": f'<html {NS}><head><title>script</title><script src="x.js"/></head>'
                    '<body><p id="text">After the script</p></body></html>',
    "anchor.xhtml": f'<html {NS}><head><title>anchor</title></head><body><a id="p1"/>'
                    '<p id="one">One</p><p id="two">Two</p></body></html>',
    "mixed.xhtml": f'<?xml version="1.0" encoding="utf-8"?><html {NS}><head><title>mixed</title></head><body>'
                   '<!-- <b>not a tag</b> --><p id="quoted" title="a &gt; b > c">Quoted</p>'
                   '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle id="dot" r="4"/></svg>'
                   '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>'
                   '<pre id="cdata"><![CDATA[<i>literal</i>]]></pre><img id="image" src="image.svg"/></body></html>',
    "entity.xhtml": '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
                    f'<html {NS}><head><title>entity</title></head><body><p id="text">A&nbsp;B</p></body></html>',
    "loose.html": '<html><head><title>loose</title></head><body><p id="text">Line<br>break &nbsp;here</body></html>',
}
with zipfile.ZipFile(path, "w") as book:
    book.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                  + "".join(f'<item id="c{i}" href="{name}"/>' for i, name in enumerate(chapters))
                  + '<item id="u" href="utf16.xhtml"/></manifest><spine>'
                  + "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
                  + '<itemref idref="u"/></spine></package>')
    for name, text in chapters.items():
        book.writestr(name, text)
    book.writestr("utf16.xhtml", b"\xff\xfe" + f'<html {NS}><head><title>utf16</title></head>'
                  '<body><p id="text">Café</p></body></html>'.encode("utf-16-le"))
    book.writestr("image.svg", '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="13"/>')

viewer = open_viewer()

def show(index, title):
    viewer._jump(index)
    wait_for(lambda: viewer._document_loaded and viewer._web.title() == title
             and not viewer._web.page().isLoading(), viewer)

def text(element="text"):
    return js(viewer, f'document.getElementById("{element}").textContent')

wait_for(lambda: viewer._document_loaded and not viewer._web.page().isLoading(), viewer)
assert js(viewer, "document.body.textContent") == "Visible chapter text"  # not swallowed by <title/>
assert js(viewer, "document.title") == ""
show(1, "script")
assert text() == "After the script"
show(2, "anchor")
assert js(viewer, 'document.getElementById("p1").childNodes.length') == 0
assert js(viewer, 'document.querySelectorAll("a p").length') == 0
show(3, "mixed")
assert js(viewer, 'document.querySelectorAll("b").length') == 0
assert js(viewer, 'document.getElementById("quoted").getAttribute("title")') == "a > b > c"
assert js(viewer, 'document.getElementById("dot").namespaceURI') == "http://www.w3.org/2000/svg"
assert js(viewer, 'document.getElementsByTagNameNS("http://www.w3.org/1998/Math/MathML", "mi").length') == 1
assert text("cdata") == "<i>literal</i>"
wait_for(lambda: js(viewer, 'document.getElementById("image").naturalWidth') == 12)
show(4, "entity")
assert text() == "A B"
show(5, "loose")  # not XML: the HTML parser still reads it
assert text() == "Linebreak  here"
show(6, "utf16")
assert text() == "Café"
''')


def test_only_well_formed_xhtml_is_read_as_xml(tmp_path):
    from vocab.gui.viewers import _renders_as_xhtml

    cases = {
        f'<html {NS}><body><p/></body></html>': True,
        '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Strict//EN" "x.dtd">'
        f'<html {NS}><body>&nbsp;&eacute;</body></html>': True,
        f'<html {NS}><body>&nbsp;</body></html>': False,         # no DTD defines it
        '<!DOCTYPE html SYSTEM "custom.dtd">'
        f'<html {NS}><body>&custom;</body></html>': False,       # unknown to Chromium
        '<html><body><p/></body></html>': False,                 # no XHTML namespace
        f'<html {NS}><body><p>unclosed</body></html>': False,    # not well formed
        f'<svg xmlns="http://www.w3.org/2000/svg"/>': False,
    }
    for index, (text, expected) in enumerate(cases.items()):
        page = tmp_path / f"page{index}.xhtml"
        page.write_text(text, encoding="utf-8")
        assert _renders_as_xhtml(page) is expected, text
