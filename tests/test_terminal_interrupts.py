"""Ctrl-C and an unusable data folder in the terminal.

Ctrl-C while a command runs cancels that command and keeps the REPL; at the
prompt it leaves, with Python's usual interrupt exit status but no traceback.
A data folder that cannot be opened is reported in one line; other startup
errors keep their traceback.

In-process tests turn a KeyboardInterrupt that escapes the loop into a test
failure, so a regression fails the test instead of stopping pytest.
"""
import io
import json
import os
import signal
import subprocess
import sys
import time

import pytest

import vocab.__main__ as entry
from vocab.cli.app import App
from vocab.cli.repl import Repl


class Keys(io.StringIO):
    """Input lines; a KeyboardInterrupt in the list is raised where Ctrl-C
    would be pressed, at the prompt that reads that line."""

    def __init__(self, *lines):
        super().__init__()
        self.lines = list(lines)

    def readline(self, *args):
        if not self.lines:
            return ""
        line = self.lines.pop(0)
        if line is KeyboardInterrupt:
            raise KeyboardInterrupt
        return line + "\n"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Repl, "_startup_checks", lambda self: None)
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    app.cmd_use("Book")
    return app


def loop(app, *lines):
    out = io.StringIO()
    try:
        Repl(app, in_stream=Keys(*lines), out_stream=out).loop()
    except KeyboardInterrupt:
        pytest.fail("Ctrl-C ended the REPL:\n" + out.getvalue())
    return out.getvalue()


def test_ctrl_c_during_a_command_cancels_only_that_command(app, monkeypatch):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(app, "cmd_stats", interrupted)
    out = loop(app, "STATS", "WHERE", "QUIT")
    assert "\nCancelled.\n" in out
    assert out.index("Cancelled.") < out.index("Current list: Book") < out.index("Goodbye.")
    assert out.rstrip().endswith("Goodbye.")


def test_ctrl_c_in_a_confirmation_cancels_it(app):
    out = loop(app, "DELETE Book", KeyboardInterrupt, "WHERE", "QUIT")
    assert "Cancelled." in out and "Current list: Book" in out
    assert app.storage.exists("Book")


def test_ctrl_c_in_study_keeps_the_answers_already_given(app):
    app.cmd_add("ephemeral", manual_def="lasting a short time")
    app.cmd_add("laconic", manual_def="using few words")
    # Reveal, "y" for the first card, then Ctrl-C at the second card's prompt.
    out = loop(app, "STUDY", "", "y", KeyboardInterrupt, "WHERE", "QUIT")
    assert "Cancelled." in out and "Current list: Book" in out
    stats = app.storage.load_stats("Book")
    assert sorted(card["box"] for card in stats.values()) == [1, 2]
    assert sorted(card["reps"] for card in stats.values()) == [0, 1]


def test_ctrl_c_in_the_startup_checks_skips_them(tmp_path, monkeypatch):
    def interrupted(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(Repl, "_startup_checks", interrupted)
    out = loop(App(tmp_path / "home"), "WHERE", "QUIT")
    assert "Skipped the startup checks; DOCTOR runs them." in out
    assert "No list selected" in out


def test_ctrl_c_at_the_prompt_says_goodbye_and_keeps_the_interrupt(app, monkeypatch):
    out = io.StringIO()
    with pytest.raises(KeyboardInterrupt):
        Repl(app, in_stream=Keys("WHERE", KeyboardInterrupt), out_stream=out).loop()
    assert out.getvalue().rstrip().endswith("Goodbye.")
    # main() passes the interrupt on to Python, with a hook that prints no
    # traceback for it and still prints other exceptions.
    monkeypatch.setattr(entry, "App", lambda home: app)
    monkeypatch.setattr(entry.Repl, "loop", lambda self: (_ for _ in ()).throw(KeyboardInterrupt))
    printed = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: printed.append(exc[0]))
    with pytest.raises(KeyboardInterrupt):
        entry.main([])
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
    sys.excepthook(RuntimeError, RuntimeError("bug"), None)
    assert printed == [RuntimeError]


def _read_until(stream, marker, timeout=60):
    data = b""
    deadline = time.monotonic() + timeout
    os.set_blocking(stream.fileno(), False)
    while marker not in data and time.monotonic() < deadline:
        chunk = stream.read()
        if chunk:
            data += chunk
        else:
            time.sleep(0.05)
    os.set_blocking(stream.fileno(), True)
    return data


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_sigint_at_the_prompt_ends_without_a_traceback(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # The online dictionary is always available, so startup downloads nothing.
    (home / "config.json").write_text(json.dumps({"active_dictionary": "freedict"}),
                                      encoding="utf-8")
    child = subprocess.Popen(
        [sys.executable, "-m", "vocab"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=dict(os.environ, VOCAB_HOME=str(home)), cwd=tmp_path,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
    try:
        started = _read_until(child.stdout, b"vocab:~> ")
        assert b"vocab:~> " in started, started
        child.send_signal(signal.SIGINT)
        rest, err = child.communicate(timeout=30)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
    out = (started + rest).decode()
    assert child.returncode == -signal.SIGINT  # what an interrupted program returns
    assert "Traceback" not in err.decode(), err.decode()
    assert out.rstrip().endswith("Goodbye.")


def test_startup_problems_are_reported_in_one_line_and_bugs_keep_their_traceback(
        tmp_path, monkeypatch):
    not_a_folder = tmp_path / "afile"
    not_a_folder.write_text("", encoding="utf-8")
    child = subprocess.run([sys.executable, "-m", "vocab"], input="QUIT\n", text=True,
                           capture_output=True, timeout=60, cwd=tmp_path,
                           env=dict(os.environ, VOCAB_HOME=str(not_a_folder)))
    assert child.returncode == 1
    lines = child.stderr.strip().splitlines()
    assert len(lines) == 1, child.stderr
    assert lines[0].startswith("Vocab Study could not start: ")
    assert str(not_a_folder) in lines[0] and "--home DIR or VOCAB_HOME" in lines[0]
    assert not_a_folder.read_text(encoding="utf-8") == ""

    def broken(home):
        raise RuntimeError("a bug")
    monkeypatch.setattr(entry, "App", broken)
    with pytest.raises(RuntimeError, match="a bug"):
        entry.main([])
