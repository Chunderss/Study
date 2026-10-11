"""Tests never use a real data folder, and neither do their child processes."""
import os
from pathlib import Path
import subprocess
import sys
import uuid

from vocab.cli.app import App
from vocab.core import paths

ROOT = Path(__file__).resolve().parents[1]


def _test_home(tmp_path_factory):
    """This test's VOCAB_HOME, checked before anything resolves a data root so
    that a failure cannot write to a real data folder."""
    home = os.environ.get("VOCAB_HOME", "")
    basetemp = tmp_path_factory.getbasetemp().resolve()
    assert home and Path(home).resolve().is_relative_to(basetemp), f"VOCAB_HOME={home!r}"
    return Path(home).resolve()


def test_an_app_without_a_root_uses_a_new_temp_folder(tmp_path_factory, tmp_path, monkeypatch):
    home = _test_home(tmp_path_factory)
    assert not any(home.iterdir())  # new for this test
    app = App()
    assert app.paths.root == home
    app.cmd_create("Book")
    assert (home / "modules" / "Book" / "module.json").is_file()
    # A test of root resolution can still choose its own folder.
    chosen = tmp_path / "chosen"
    monkeypatch.setenv("VOCAB_HOME", str(chosen))
    assert App().paths.root == chosen.resolve()


def test_child_processes_use_the_test_data_folder(tmp_path_factory):
    home = _test_home(tmp_path_factory)
    result = subprocess.run(
        [sys.executable, "-c", "from vocab.core.paths import get_paths; print(get_paths().root)"],
        cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == home


def test_a_developer_vocab_home_is_left_alone(tmp_path):
    real = tmp_path / "real-data"
    real.mkdir()
    (real / "config.json").write_text('{"keep": true}', encoding="utf-8")
    test = f"{Path(__file__).name}::test_an_app_without_a_root_uses_a_new_temp_folder"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         f"--basetemp={tmp_path / 'basetemp'}", f"tests/{test}"],
        cwd=ROOT, env=dict(os.environ, VOCAB_HOME=str(real)),
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert [p.name for p in real.iterdir()] == ["config.json"]
    assert (real / "config.json").read_text(encoding="utf-8") == '{"keep": true}'


def test_child_processes_use_the_test_documents_folder(documents_folder, tmp_path):
    # A child with its own data folder, writing to relative paths. Its home
    # is a stand-in for the developer's, where a child that missed the
    # test's Documents folder would write on Linux and macOS; on Windows
    # such a child would write to the real Documents folder, so the names
    # are new and anything found there is removed.
    data, home = tmp_path / "data", tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    name = f"isolation-{uuid.uuid4().hex}"
    real = [paths.platform_documents_folder() / f"{name}.{suffix}" for suffix in ("json", "zip")]
    commands = f"CREATE Book\nEXPORT Book {name}.json\nBACKUP Book {name}.zip\nQUIT\n"
    try:
        child = subprocess.run([sys.executable, "-m", "vocab"], input=commands, text=True,
                               capture_output=True, timeout=60, cwd=tmp_path,
                               env=dict(os.environ, VOCAB_HOME=str(data), HOME=str(home),
                                        USERPROFILE=str(home)))
        assert f"Exported 'Book' -> {documents_folder / f'{name}.json'}" in child.stdout, \
            child.stdout + child.stderr
        assert f"-> {documents_folder / f'{name}.zip'} (" in child.stdout
        assert sorted(p.name for p in documents_folder.iterdir()) == [f"{name}.json", f"{name}.zip"]
        assert [p.relative_to(home) for p in home.rglob("*")] == [Path("Documents")]
        assert not any(path.exists() for path in real)
    finally:
        for path in real:
            if path.is_file():
                path.unlink()
