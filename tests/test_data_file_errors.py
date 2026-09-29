"""Damaged or hand-edited data files are named, preserved and isolated."""
import json
import os
import sys

import pytest

from vocab.cli.app import App
from vocab.core.storage import DataFileError


def make(tmp_path, *names):
    app = App(tmp_path)
    for name in names:
        app.cmd_create(name)
        app.cmd_add("word", target=name, manual_def="a unit of language")
    return app


def test_utf8_bom_module_and_config_files_load(tmp_path):
    app = make(tmp_path, "Book")
    words = app.paths.words_file("Book")
    words.write_bytes(b"\xef\xbb\xbf" + words.read_bytes())
    config = app.paths.config_file
    config.write_bytes(b"\xef\xbb\xbf" + json.dumps({"judge_backend": "keyword",
                                                   "mechanical_repetition": True}).encode())
    reopened = App(tmp_path)
    assert reopened.storage.load_words("Book").has("word")
    assert reopened.config.mechanical_repetition is True
    assert not reopened.config.load_error


@pytest.mark.parametrize("filename, content", [
    ("words.json", b'{"name": "Book", "words": {'),
    ("words.json", b'["not", "an", "object"]'),
    ("words.json", b'{"name": "Book", "words": ["word"]}'),
    ("stats.json", b'{"word": '),
    ("stats.json", b'[1, 2]'),
])
def test_damaged_files_are_named_and_left_unchanged(tmp_path, filename, content):
    app = make(tmp_path, "Book")
    path = app.paths.words_file("Book").with_name(filename)
    path.write_bytes(content)
    with pytest.raises(DataFileError) as info:
        app.due_count("Book")
    assert str(path) in str(info.value)
    assert info.value.path == path
    assert path.read_bytes() == content


def test_one_unreadable_module_does_not_hide_the_others(tmp_path):
    app = make(tmp_path, "Alpha", "Beta")
    bad = app.paths.words_file("Alpha")
    bad.write_text("{", encoding="utf-8")
    listing = app.cmd_lists()
    assert "Beta  (1 words)" in listing
    assert "Alpha  (unreadable:" in listing and str(bad) in listing


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions enforced for this user")
def test_permission_errors_name_the_file(tmp_path):
    app = make(tmp_path, "Alpha", "Beta")
    bad = app.paths.words_file("Alpha")
    bad.chmod(0)
    try:
        listing = app.cmd_lists()
    finally:
        bad.chmod(0o644)
    assert "Alpha  (unreadable:" in listing and str(bad) in listing
    assert "Beta  (1 words)" in listing


def test_unreadable_config_runs_on_defaults_and_keeps_the_original(tmp_path):
    App(tmp_path)
    config = tmp_path / "config.json"
    original = b'{"active_dictionary": "wordnet",'
    config.write_bytes(original)
    app = App(tmp_path)
    assert str(config) in app.config.load_error
    assert app.config.judge_backend == "keyword"
    assert config.read_bytes() == original
    # An explicit settings change may replace it, but only after keeping a copy.
    app.config.save(app.paths)
    assert (tmp_path / "config.unreadable.json").read_bytes() == original
    assert json.loads(config.read_text(encoding="utf-8"))["judge_backend"] == "keyword"
    assert not App(tmp_path).config.load_error


def test_folders_with_unsupported_names_are_reported_not_used(tmp_path):
    app = make(tmp_path, "Good")
    renamed = app.paths.modules_dir / "Beta [old]"
    app.paths.module_dir("Good").rename(renamed)
    app.cmd_create("Good")
    assert app.storage.list_names() == ["Good"]
    assert app.storage.unsupported_module_dirs() == ["Beta [old]"]
    assert app.study_targets_all() == ["Good"]
    assert "Beta [old]  (folder name not supported" in app.cmd_lists()
