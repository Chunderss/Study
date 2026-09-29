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
    original = b'{"active_dictionary": "wordnet", "mechanical_repetition": true,'
    config.write_bytes(original)
    app = App(tmp_path)
    assert str(config) in app.config.load_error
    assert app.config.mechanical_repetition is False
    # Settings changes must not silently replace the file with defaults.
    with pytest.raises(ValueError, match="Fix or delete that file"):
        app.cmd_disambig("base")
    with pytest.raises(ValueError, match="Fix or delete that file"):
        app.config.change(app.paths, mechanical_repetition=True)
    assert app.config.disambiguator == "nlp" and app.config.mechanical_repetition is False
    assert config.read_bytes() == original
    # Deleting the file is an explicit reset: saving then starts a fresh one.
    config.unlink()
    app.config.change(app.paths, mechanical_repetition=True)
    assert json.loads(config.read_text(encoding="utf-8"))["mechanical_repetition"] is True
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


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions enforced for this user")
def test_module_folder_that_cannot_be_opened_is_listed_as_unreadable(tmp_path):
    app = make(tmp_path, "Alpha", "Beta")
    folder = app.paths.module_dir("Alpha")
    folder.chmod(0)
    try:
        assert app.storage.list_names() == ["Alpha", "Beta"]
        listing = app.cmd_lists()
        assert "Alpha  (unreadable:" in listing and str(folder) in listing
        assert "Beta  (1 words)" in listing
        assert app.cmd_use("Beta") == "Now using 'Beta'."
    finally:
        folder.chmod(0o755)


@pytest.mark.parametrize("content", [b"null", b"[]", b'"module"'])
def test_manifest_with_non_object_json_is_reported_not_replaced(tmp_path, content):
    app = make(tmp_path, "Book")
    manifest = app.paths.manifest_file("Book")
    manifest.write_bytes(content)
    with pytest.raises(DataFileError) as info:
        app.storage.load_module("Book")
    assert info.value.path == manifest
    assert manifest.read_bytes() == content


def test_missing_manifest_is_still_created(tmp_path):
    app = make(tmp_path, "Book")
    manifest = app.paths.manifest_file("Book")
    manifest.unlink()
    assert app.storage.load_module("Book").name == "Book"
    assert manifest.is_file()


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and symlinks for this user")
def test_config_that_cannot_even_be_checked_is_treated_as_unreadable(tmp_path):
    home = tmp_path / "home"
    App(home)
    config = home / "config.json"
    config.unlink()
    folder = tmp_path / "locked"
    folder.mkdir()
    (folder / "settings.json").write_text('{"mechanical_repetition": true}', encoding="utf-8")
    config.symlink_to(folder / "settings.json")
    folder.chmod(0)
    try:
        app = App(home)
        assert str(config) in app.config.load_error
        with pytest.raises(ValueError, match="Fix or delete that file"):
            app.config.change(app.paths, mechanical_repetition=False)
        assert config.is_symlink()
    finally:
        folder.chmod(0o700)
    assert (folder / "settings.json").read_text(encoding="utf-8") == '{"mechanical_repetition": true}'
