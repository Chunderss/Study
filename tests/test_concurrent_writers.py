"""Processes sharing a data folder must not lose each other's changes.

Each worker runs in its own process and pauses after reading, inside its
read-check-write sequence. Without coordination both read the same old state
and the later write drops the earlier change.
"""
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from vocab.cli.app import App
from vocab.core.config import Config
from vocab.core.learning import LearningStore
from vocab.core.locking import DataFolderBusy, write_lock
from vocab.core.paths import get_paths
from vocab.core.storage import Storage

ROOT = Path(__file__).resolve().parents[1]


def _wait_until(start_at):
    while time.time() < start_at:
        time.sleep(0.005)


def _add_word(root, word, start_at, out):
    app = App(root)
    load = app.storage.load_words
    def slow_load(name):
        words = load(name)
        time.sleep(0.3)  # widen the gap between reading and writing
        return words
    app.storage.load_words = slow_load
    _wait_until(start_at)
    try:
        out.put(app.cmd_add(word, target="Book", manual_def="meaning of " + word))
    except Exception as error:
        out.put(f"{type(error).__name__}: {error}")


def _review(root, item_id, revision, answer, start_at, out):
    store = LearningStore(Storage(get_paths(root)))
    load = store.load
    def slow_load(module):
        items = load(module)
        time.sleep(0.3)
        return items
    store.load = slow_load
    _wait_until(start_at)
    try:
        store.review("Book", item_id, revision, answer, "good", now=100000)
        out.put("success")
    except Exception as error:
        out.put(f"{type(error).__name__}: {error}")


def _change_setting(root, name, value, start_at, out):
    app = App(root)
    load = Config.load.__func__
    def slow_load(cls, paths):
        config = load(cls, paths)
        time.sleep(0.3)
        return config
    Config.load = classmethod(slow_load)
    _wait_until(start_at)
    try:
        app.config.change(app.paths, **{name: value})
        out.put("success")
    except Exception as error:
        out.put(f"{type(error).__name__}: {error}")


def _run_together(target, argument_sets):
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    start_at = time.time() + 3  # after both processes have started
    processes = [ctx.Process(target=target, args=(*args, start_at, out)) for args in argument_sets]
    for process in processes:
        process.start()
    for process in processes:
        process.join(30)
        assert process.exitcode == 0, process.exitcode
    return [out.get(timeout=5) for _ in processes]


def test_two_processes_adding_words_keep_both(tmp_path):
    App(tmp_path).cmd_create("Book")
    results = _run_together(_add_word, [(tmp_path, "alpha"), (tmp_path, "beta")])
    assert all(result.startswith("Added") for result in results), results
    words = App(tmp_path).storage.load_words("Book").words
    assert sorted(words) == ["alpha", "beta"]


def test_two_processes_reviewing_the_same_revision_cannot_both_succeed(tmp_path):
    app = App(tmp_path)
    app.cmd_create("Book")
    item = LearningStore(app.storage).save_capture("Book", kind="concept", prompt="Why?",
                                                   quote="A passage.")
    results = _run_together(_review, [(tmp_path, item["id"], item["revision"], "First"),
                                      (tmp_path, item["id"], item["revision"], "Second")])
    assert sorted(result == "success" for result in results) == [False, True], results
    saved = LearningStore(app.storage).load("Book")[item["id"]]
    assert len(saved["attempts"]) == 1 and saved["revision"] == item["revision"] + 1


def test_two_processes_changing_different_settings_keep_both(tmp_path):
    App(tmp_path)
    results = _run_together(_change_setting, [(tmp_path, "disambiguator", "base"),
                                              (tmp_path, "mechanical_repetition", True)])
    assert results == ["success", "success"], results
    config = App(tmp_path).config
    assert config.disambiguator == "base" and config.mechanical_repetition is True


def test_lock_is_reentrant_and_separate_per_data_folder(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    acquired = []

    def take_second():
        with write_lock(second).hold(timeout=1):
            acquired.append(True)

    with write_lock(first).hold():
        with write_lock(first).hold():  # nested transactions in one thread
            thread = threading.Thread(target=take_second)
            thread.start()
            thread.join(5)
    assert acquired == [True]  # another folder is not blocked


HOLDER = r"""
import sys, time
from vocab.core.locking import write_lock
with write_lock(sys.argv[1]).hold():
    print("HELD", flush=True)
    if sys.argv[2] == "crash":
        import os
        os._exit(0)
    time.sleep(float(sys.argv[2]))
"""


def _holder(root, mode):
    process = subprocess.Popen([sys.executable, "-c", HOLDER, str(root), mode], cwd=ROOT,
                               stdout=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "HELD"
    return process


def test_busy_folder_reports_a_clear_error_and_a_crash_releases_the_lock(tmp_path):
    holder = _holder(tmp_path, "3")
    try:
        with pytest.raises(DataFolderBusy, match="Try again in a moment"):
            with write_lock(tmp_path).hold(timeout=0.3):
                pass
    finally:
        holder.wait(10)
    crashed = _holder(tmp_path, "crash")
    crashed.wait(10)
    started = time.monotonic()
    with write_lock(tmp_path).hold(timeout=2):
        pass
    assert time.monotonic() - started < 1


def test_recovery_copies_take_the_lock_briefly_and_reads_do_not_wait(tmp_path):
    from vocab.study.session import build_session
    app = App(tmp_path)
    app.cmd_create("Book")
    app.cmd_add("word", target="Book", manual_def="meaning")
    holder = _holder(tmp_path, "4")
    try:
        started = time.monotonic()
        with pytest.raises(DataFolderBusy):
            app.storage.save_note_draft("Book", "One", "", "draft")
        assert 0.8 < time.monotonic() - started < 3  # gives up after about a second
        started = time.monotonic()
        App(tmp_path)  # nothing to migrate
        build_session(app.storage, app.scheduler, ["Book"])  # nothing to backfill
        assert time.monotonic() - started < 1
    finally:
        holder.wait(10)
    assert isinstance(DataFolderBusy("busy"), OSError)
