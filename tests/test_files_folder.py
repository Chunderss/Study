"""Relative paths in file commands start in the folder for files.

That folder is the user's Documents folder unless FOLDER chose another; the
working directory, which depends on how the program was started, plays no
part. A full path is used as typed and '~' is the home folder. The conftest
points the Documents folder at a temporary folder for every test.
"""
import io
import json
import os
import subprocess
import sys

import pytest

from vocab.cli.app import App
from vocab.cli.parser import parse
from vocab.cli.repl import Repl
from vocab.core import paths


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Repl, "_startup_checks", lambda self: None)
    app = App(tmp_path / "home")
    app.cmd_create("Book")
    app.cmd_use("Book")
    app.cmd_add("ephemeral", manual_def="lasting a short time")
    return app


def run(app, line):
    return Repl(app, in_stream=io.StringIO(), out_stream=io.StringIO()).dispatch(parse(line))


def loop(app, *lines):
    out = io.StringIO()
    Repl(app, in_stream=io.StringIO("\n".join(lines + ("QUIT",)) + "\n"), out_stream=out).loop()
    return out.getvalue()


def test_relative_paths_start_in_the_files_folder_not_the_working_directory(
        app, tmp_path, monkeypatch, documents_folder):
    cwd = tmp_path / "started here"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    docs = documents_folder
    assert run(app, "EXPORT Book book.json") == f"Exported 'Book' -> {docs / 'book.json'}"
    assert (docs / "book.json").is_file() and list(cwd.iterdir()) == []
    assert run(app, "EXPORT Book sub/../up.json") == f"Exported 'Book' -> {docs / 'up.json'}"
    assert "from " + str(docs / "book.json") in run(app, "IMPORT book.json as Copy")
    assert "ephemeral" in app.storage.load_words("Copy").words
    (docs / "broken.json").write_text("{bad", encoding="utf-8")
    out = loop(app, "IMPORT missing.json", "IMPORT broken.json", "EXPORT Book book.json/x.json")
    assert f"! Import file not found: {docs / 'missing.json'}" in out
    assert f"! Could not read {docs / 'broken.json'}" in out
    assert f"! Could not write {docs / 'book.json' / 'x.json'}" in out  # book.json is a file
    # '~' is the home folder, wherever the folder for files is.
    home = tmp_path / "pat"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    assert run(app, "EXPORT Book ~/home.json") == f"Exported 'Book' -> {home / 'home.json'}"
    assert (home / "home.json").is_file()
    # A path typed in full is used, and shown, as typed.
    full = tmp_path / "elsewhere" / "full.json"
    full.parent.mkdir()
    assert run(app, f'EXPORT Book "{full}"') == f"Exported 'Book' -> {full}"
    if os.name != "nt":
        (tmp_path / "linked").symlink_to(full.parent, target_is_directory=True)
        typed = tmp_path / "linked" / "via-link.json"
        assert run(app, f'EXPORT Book "{typed}"') == f"Exported 'Book' -> {typed}"
        assert (full.parent / "via-link.json").is_file()
    assert list(cwd.iterdir()) == []


def test_folder_shows_sets_and_resets(app, tmp_path, documents_folder):
    shown = run(app, "FOLDER")
    assert f"start in {documents_folder} (your Documents folder)" in shown
    chosen = tmp_path / "My Exports"
    chosen.mkdir()
    out = loop(app, "FOLDER exports", f"FOLDER {tmp_path / 'missing'}", "FOLDER a b")
    assert "! FOLDER needs a full path" in out
    assert f"! {tmp_path / 'missing'} is not a folder that exists" in out
    assert "Usage: FOLDER" in out
    assert app.config.files_folder == ""
    assert run(app, f'FOLDER "{chosen}"') == f"Relative file paths now start in {chosen}."
    stored = json.loads(app.paths.config_file.read_text(encoding="utf-8"))
    assert stored["files_folder"] == str(chosen)
    assert run(app, "EXPORT Book book.json") == f"Exported 'Book' -> {chosen / 'book.json'}"
    # The setting belongs to the data folder: a new App (the desktop) uses it.
    other = App(app.paths.root)
    assert other.files_folder() == chosen
    assert f"start in {chosen}." in run(other, "FOLDER")
    assert run(app, "FOLDER default") == \
        f"Relative file paths now start in your Documents folder, {documents_folder}."
    assert json.loads(app.paths.config_file.read_text(encoding="utf-8"))["files_folder"] == ""
    # A config.json that cannot be read is never replaced by a FOLDER change.
    app.paths.config_file.write_text("{bad", encoding="utf-8")
    broken = App(app.paths.root)
    assert "Settings were not changed" in loop(broken, f'FOLDER "{chosen}"')
    assert app.paths.config_file.read_text(encoding="utf-8") == "{bad"


@pytest.mark.parametrize("setting, named", [
    ("removed", "is not available"),
    ("rel/x", "is not a full path"),
    (5, "is not a path (5)"),
])
def test_an_unavailable_or_invalid_folder_is_never_replaced_silently(
        app, tmp_path, documents_folder, setting, named):
    if setting == "removed":
        folder = tmp_path / "usb"
        folder.mkdir()
        run(app, f"FOLDER {folder}")
        folder.rmdir()  # the drive was unplugged
        setting = str(folder)
    else:
        config = json.loads(app.paths.config_file.read_text(encoding="utf-8"))
        config["files_folder"] = setting
        app.paths.config_file.write_text(json.dumps(config), encoding="utf-8")
    app = App(app.paths.root)
    out = loop(app, "EXPORT Book book.json", "IMPORT book.json as Copy", "FOLDER")
    assert out.count(named) == 3, out
    where = str(setting) if setting != 5 else str(app.paths.config_file)
    assert where in out and "FOLDER" in out
    assert list(documents_folder.iterdir()) == []  # never a silent fallback
    assert not (tmp_path / "rel").exists() and not (tmp_path / "usb").exists()
    full = tmp_path / "full.json"
    assert run(app, f"EXPORT Book {full}") == f"Exported 'Book' -> {full}"  # full paths work


@pytest.mark.skipif(sys.platform in ("win32", "darwin"), reason="the XDG user folders")
def test_platform_documents_folder(tmp_path, monkeypatch):
    home = tmp_path / "home"
    config = tmp_path / "config"
    config.mkdir(parents=True)
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    user_dirs = config / "user-dirs.dirs"
    assert paths.platform_documents_folder() == home  # nothing set, no ~/Documents
    (home / "Documents").mkdir()
    assert paths.platform_documents_folder() == home / "Documents"
    user_dirs.write_text('# comment\nXDG_DESKTOP_DIR="$HOME/Desktop"\n'
                         'XDG_DOCUMENTS_DIR="$HOME/Dokumente"\n', encoding="utf-8")
    assert paths.platform_documents_folder() == home / "Documents"  # Dokumente is missing
    (home / "Dokumente").mkdir()
    assert paths.platform_documents_folder() == home / "Dokumente"
    absolute = tmp_path / "Shared Docs"
    absolute.mkdir()
    user_dirs.write_text(f'XDG_DOCUMENTS_DIR="{absolute}"\n', encoding="utf-8")
    assert paths.platform_documents_folder() == absolute
    user_dirs.write_text('XDG_DOCUMENTS_DIR="$HOME/"\n', encoding="utf-8")  # disabled
    assert paths.platform_documents_folder() == home


def test_path_forms(tmp_path, monkeypatch):
    base = tmp_path / "docs"
    calls = []

    def folder():
        calls.append(1)
        return base
    full = tmp_path / "x.json"
    assert paths.resolve_user_path(str(full), folder) == full
    assert calls == []  # a full path never consults the folder setting
    monkeypatch.setenv("HOME", str(tmp_path / "pat"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "pat"))
    assert paths.resolve_user_path("~", folder) == tmp_path / "pat"
    assert paths.resolve_user_path("~/a.json", folder) == tmp_path / "pat" / "a.json"
    assert calls == []
    assert paths.resolve_user_path("~pat/a.json", folder) == base / "~pat" / "a.json"
    assert paths.resolve_user_path("a/../../b.json", folder) == tmp_path / "b.json"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows folders and path forms")
def test_documents_folder_matches_windows(tmp_path):
    for typed in (r"D:book.json", r"\book.json"):
        with pytest.raises(ValueError, match="give a full path"):
            paths.resolve_user_path(typed, tmp_path)
    QtCore = pytest.importorskip("PySide6.QtCore")
    expected = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)
    assert os.path.normcase(paths.platform_documents_folder()) == \
        os.path.normcase(os.path.normpath(expected))


def test_terminal_relative_paths_use_the_chosen_folder_from_any_directory(tmp_path):
    # The real terminal, started in one folder with the folder for files set
    # to another. (A child process does not see the conftest's Documents
    # folder, so it is given a chosen folder.)
    home = tmp_path / "home"
    files = tmp_path / "files"
    started = tmp_path / "started"
    for folder in (home, files, started):
        folder.mkdir()
    (home / "config.json").write_text(json.dumps(
        {"active_dictionary": "freedict", "files_folder": str(files)}), encoding="utf-8")
    commands = "CREATE Book\nADDL Book ephemeral :: brief\nEXPORT Book book.json\nQUIT\n"
    child = subprocess.run([sys.executable, "-m", "vocab"], input=commands, text=True,
                           capture_output=True, timeout=60, cwd=started,
                           env=dict(os.environ, VOCAB_HOME=str(home)))
    assert f"Exported 'Book' -> {files / 'book.json'}" in child.stdout, child.stdout + child.stderr
    assert (files / "book.json").is_file()
    assert list(started.iterdir()) == []
