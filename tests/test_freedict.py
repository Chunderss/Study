"""FreeDictionary retries a dropped or garbled reply, and only that (L8).

urlopen is replaced, but replies are parsed by the real http.client, so the
errors are the ones a broken connection actually produces.
"""
import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from vocab.cli.app import App
from vocab.dictionaries.base import LookupFailed
from vocab.dictionaries.freedict import FreeDictionary

BODY = json.dumps([{"meanings": [{"partOfSpeech": "adjective",
                                  "definitions": [{"definition": "using very few words"}]}]}]).encode()


class _Socket:
    def __init__(self, raw):
        self.raw = raw

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.raw)


def reply(raw):
    response = http.client.HTTPResponse(_Socket(raw), method="GET")
    response.begin()
    return response


def ok(body):
    return b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\n\r\n" % len(body) + body


TRUNCATED = b"HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n" + BODY[:12]


@pytest.fixture
def server(monkeypatch):
    """Serve the queued replies (bytes, or an exception to raise) in order."""
    queue, attempts, sleeps = [], [], []

    def urlopen(request, timeout=None):
        attempts.append(request.full_url)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return reply(item)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr("vocab.dictionaries.freedict.time.sleep", sleeps.append)
    return queue, attempts, sleeps


@pytest.mark.parametrize("bad", [
    TRUNCATED,                                   # http.client.IncompleteRead
    b"XYZZY garbage\r\n\r\n",                     # http.client.BadStatusLine
    ok(b'[{"meanings": "\x80\x81"}]'),           # UnicodeDecodeError, not JSONDecodeError
], ids=["truncated-body", "garbled-status-line", "invalid-utf8"])
def test_unreadable_reply_is_retried(server, bad):
    queue, attempts, sleeps = server
    queue += [bad, ok(BODY)]
    senses = FreeDictionary().lookup("laconic")
    assert [s.definition for s in senses] == ["using very few words"]
    assert len(attempts) == 2 and sleeps == [0.6]


def test_lasting_truncation_fails_recoverably_with_the_manual_hint(server, tmp_path):
    queue, attempts, sleeps = server
    queue.append(TRUNCATED)
    app = App(root=str(tmp_path))
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.config.active_dictionary = "freedict"
    with pytest.raises(ValueError) as caught:
        app.cmd_add("laconic")
    assert "add manually:  ADD laconic :: <your definition>" in str(caught.value)
    assert len(attempts) == 3 and sleeps == [0.6, 1.2]  # bounded: retries=2
    assert not app.storage.load_words("Book").words


def test_permanent_and_internal_errors_are_not_retried(server):
    """Only a failure that another attempt may cure is retried."""
    queue, attempts, _ = server

    def outcome(item):
        queue[:] = [item]
        attempts.clear()
        try:
            FreeDictionary().lookup("laconic")
        except LookupFailed as error:
            return len(attempts), "recoverable" if error.recoverable else "permanent"
        except Exception as error:
            return len(attempts), type(error).__name__

    not_found = urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    assert outcome(not_found) == (1, "permanent")
    unavailable = urllib.error.HTTPError("u", 503, "Service Unavailable", {}, None)
    assert outcome(unavailable) == (3, "recoverable")         # retried, as before
    assert outcome(ok(b"[]")) == (1, "permanent")             # no definitions at all
    # The service's reply is valid JSON but malformed: worth trying later.
    assert outcome(ok(b'["oops"]')) == (1, "recoverable")
    assert outcome(ok(b'[{"meanings": 5}]')) == (1, "recoverable")
    assert outcome(ok(b'{"title": "Unexpected"}')) == (1, "recoverable")
    assert outcome(ok(b'[{"meanings": [{"definitions": ["text"]}]}]')) == (1, "recoverable")
    # Our own mistakes surface as themselves, not as a network problem.
    assert outcome(ValueError("unknown url type: 'htps'")) == (1, "ValueError")


def test_fields_of_the_wrong_type_are_skipped(server):
    queue, attempts, _ = server
    queue.append(ok(json.dumps([{"meanings": [{"partOfSpeech": 5, "definitions": [
        {"definition": 7}, {"definition": " using very few words ", "example": None}]}]}]).encode()))
    senses = FreeDictionary().lookup("laconic")
    assert [(s.pos, s.definition, s.example) for s in senses] == [("", "using very few words", "")]
    assert len(attempts) == 1

