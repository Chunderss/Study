"""First startup must not replace settings another process just saved."""
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from vocab.core.config import Config
from vocab.core.paths import get_paths


ROOT = Path(__file__).resolve().parents[1]
FIRST_START = r"""
import json, sys, time
from pathlib import Path
from vocab.core.config import Config
from vocab.core.paths import get_paths
paths = get_paths(sys.argv[1])
ready, resume = Path(sys.argv[2]), Path(sys.argv[3])
exists = Path.exists
paused = False
def check(path):
    global paused
    present = exists(path)
    if path == paths.config_file and not present and not paused:
        paused = True
        ready.write_text("ready", encoding="utf-8")
        deadline = time.monotonic() + 15
        while not exists(resume):
            if time.monotonic() > deadline:
                raise TimeoutError("first-start test was not released")
            time.sleep(0.01)
    return present
Path.exists = check
config = Config.load(paths)
print(json.dumps([config.disambiguator, config.mechanical_repetition]))
"""


def test_first_start_rechecks_after_another_process_saves(tmp_path):
    paths = get_paths(tmp_path / "home")
    ready, resume = tmp_path / "ready", tmp_path / "resume"
    child = subprocess.Popen([sys.executable, "-c", FIRST_START, str(paths.root),
                              str(ready), str(resume)], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "child did not reach the missing-config check"
        other = Config.load(paths)
        other.change(paths, disambiguator="base", mechanical_repetition=True)
        committed = paths.config_file.read_bytes()
        resume.write_text("resume", encoding="utf-8")
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, stderr
        assert json.loads(stdout) == ["base", True]
        assert paths.config_file.read_bytes() == committed
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_failed_first_write_is_not_reported_as_unreadable_config(tmp_path, monkeypatch):
    from vocab.core import storage
    paths = get_paths(tmp_path)
    def fail(path, data):
        raise PermissionError("cannot create settings")
    monkeypatch.setattr(storage, "_atomic_write", fail)
    with pytest.raises(PermissionError, match="cannot create settings"):
        Config.load(paths)
    assert not paths.config_file.exists()
