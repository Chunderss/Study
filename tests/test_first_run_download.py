"""The terminal's first-run WordNet download is bounded and can be skipped.

The policy itself is unchanged: when WordNet is the configured dictionary but
is missing, the terminal downloads it at startup, and tries again at the next
start. These tests use the real NLTK downloader against a local server.
"""
import io
import socket
import threading
import warnings

import pytest

from vocab.cli.app import App
from vocab.cli.repl import Repl


def loop(app, *lines):
    out = io.StringIO()
    repl = Repl(app, in_stream=io.StringIO("\n".join(lines + ("QUIT",)) + "\n"),
                out_stream=out)
    try:
        repl.loop()
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C ended the REPL:\n" + out.getvalue())
    return out.getvalue()


@pytest.fixture
def silent_server():
    """A server that accepts connections and never answers, like a network
    that drops packets after the handshake."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)
    accepted = []

    def accept():
        while True:
            try:
                accepted.append(server.accept()[0])
            except OSError:
                return
    threading.Thread(target=accept, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}/index.xml"
    server.close()
    for connection in accepted:  # releases a download still waiting on them
        connection.close()


def test_first_run_download_gives_up_on_a_silent_network(tmp_path, monkeypatch, silent_server):
    pytest.importorskip("nltk")
    import nltk.downloader as downloader
    from vocab.dictionaries import wordnet
    try:
        import nltk.pathsec as pathsec
        monkeypatch.setattr(pathsec, "ENFORCE", False, raising=False)  # 3.10 refuses loopback
    except ImportError:
        pass
    monkeypatch.setattr(downloader._downloader, "_url", silent_server)
    monkeypatch.setattr(downloader._downloader, "_index", None)
    monkeypatch.setattr(downloader._downloader, "_download_dir", str(tmp_path / "nltk_data"))
    monkeypatch.setattr(wordnet.WordNet, "available", lambda self: False)
    monkeypatch.setattr(wordnet, "DOWNLOAD_TIMEOUT", 0.5, raising=False)
    previous = socket.getdefaulttimeout()
    app = App(tmp_path / "home")
    result = {}

    def run():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result["out"] = loop(app, "WHERE")
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(15)
    assert not worker.is_alive(), "startup waited for a network that never answers"
    out = result["out"]
    assert wordnet.WordNet().install_cost in out  # DICT INSTALL's size text
    assert "(Ctrl-C skips it)" in out
    assert "could not download automatically" in out and "timed out" in out
    assert "No list selected" in out  # the prompt was reached
    assert socket.getdefaulttimeout() == previous


def test_ctrl_c_skips_the_first_run_download(tmp_path, monkeypatch):
    pytest.importorskip("nltk")
    from vocab.dictionaries.wordnet import WordNet
    monkeypatch.setattr(WordNet, "available", lambda self: False)

    def interrupted(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(WordNet, "install", interrupted)
    previous = socket.getdefaulttimeout()
    out = loop(App(tmp_path / "home"), "WHERE")
    assert "(Ctrl-C skips it)" in out
    assert "  skipped. DICT INSTALL wordnet downloads it later." in out
    assert "No list selected" in out
    assert socket.getdefaulttimeout() == previous


def test_the_download_timeout_is_restored_however_the_install_ends(monkeypatch):
    nltk = pytest.importorskip("nltk")
    from vocab.dictionaries import wordnet
    seen = []

    def download(package, **kwargs):
        seen.append(socket.getdefaulttimeout())
        raise KeyboardInterrupt
    monkeypatch.setattr(nltk, "download", download)
    monkeypatch.setattr(wordnet.WordNet, "available", lambda self: False)
    previous = socket.getdefaulttimeout()
    with pytest.raises(KeyboardInterrupt):
        wordnet.WordNet().install()
    assert seen == [wordnet.DOWNLOAD_TIMEOUT]
    assert socket.getdefaulttimeout() == previous
