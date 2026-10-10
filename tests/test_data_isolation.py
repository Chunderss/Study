"""Tests never use a real data folder, and neither do their child processes."""
import os
from pathlib import Path
import subprocess
import sys

from vocab.cli.app import App

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
