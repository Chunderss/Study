"""Reveal mode in the terminal asks for the verdict as mechanical mode does:
Enter means missed, y or n choose, q stops without recording the card, and any
other reply asks again."""
import pytest

from vocab.cli.app import App

VERDICT = "  did you get it? [y/N] (or 'q'): "


@pytest.fixture
def app(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.config.mechanical_repetition = False
    for word, definition, box in (("alpha", "first letter", 1), ("beta", "second letter", 2)):
        app.cmd_add(word, manual_def=definition)
        stats = app.storage.load_stats("Book")
        stats[word].update(box=box, due=0.0)
        app.storage.save_stats("Book", stats)
    return app


def scripted(replies):
    asked = []
    replies = iter(replies)

    def prompt(text):
        asked.append(text)
        return next(replies)

    return prompt, asked


def test_q_at_the_reveal_verdict_stops_without_recording(app):
    before = app.storage.load_stats("Book")
    prompt, asked = scripted(["", "q", "q"])
    summary = app.run_study(["Book"], prompt, lambda _line: None)
    assert asked == ["  press Enter to reveal (or 'q'): ", VERDICT]
    assert app.storage.load_stats("Book") == before
    assert summary.endswith("Session done: 0/0 correct (0%).")


def test_the_reveal_verdict_needs_a_clear_reply_and_enter_means_missed(app):
    prompt, asked = scripted(["", "maybe", "  Yes ", "", ""])
    summary = app.run_study(["Book"], prompt, lambda _line: None)
    assert asked.count(VERDICT) == 3          # "maybe" asked again
    stats = app.storage.load_stats("Book")
    assert (stats["alpha"]["box"], stats["beta"]["box"]) == (2, 1)
    assert stats["beta"]["lapses"] == 1
    assert summary.endswith("Session done: 1/2 correct (50%).")


def test_a_failed_reveal_save_asks_for_the_verdict_again(app, monkeypatch):
    save = app.storage.save_stats
    failures = [OSError("disk full")]

    def save_once_failing(*args):
        if failures:
            raise failures.pop()
        save(*args)

    monkeypatch.setattr(app.storage, "save_stats", save_once_failing)
    output = []
    prompt, asked = scripted(["", "y", "y", "", "n"])
    summary = app.run_study(["Book"], prompt, output.append)
    assert "  Could not save review: disk full" in output
    assert asked.count(VERDICT) == 3
    stats = app.storage.load_stats("Book")
    assert (stats["alpha"]["box"], stats["beta"]["box"]) == (2, 1)
    assert summary.endswith("Session done: 1/2 correct (50%).")
