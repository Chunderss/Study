"""Settings every test gets, which child processes inherit through os.environ.

No test may use a real data folder, and every child Python a test starts must
import this checkout, whichever directory pytest was started from. vocab is a
namespace package, so a child otherwise imports the vocab/ its working
directory or an installed copy of another checkout provides.
"""
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def child_python_path(monkeypatch):
    """Put this checkout first on PYTHONPATH for child Python processes.

    A test whose child must not find this checkout has to remove PYTHONPATH
    from that child's environment itself.
    """
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(
        filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])))


@pytest.fixture(autouse=True)
def vocab_home(tmp_path_factory, monkeypatch):
    """Point VOCAB_HOME at a new temporary folder for each test.

    App(), MainWindow() and get_paths() without a root, and child processes,
    then use it instead of ~/.vocab or %LOCALAPPDATA%\\VocabStudy, even when the
    developer's VOCAB_HOME names real data. A test of root resolution can
    still set VOCAB_HOME itself with monkeypatch; one that removes it must
    also point HOME (and LOCALAPPDATA on Windows) at a temporary folder.
    HOME and NLTK_DATA are left alone, so NLTK finds the developer's data.
    """
    home = tmp_path_factory.mktemp("vocab-home")
    monkeypatch.setenv("VOCAB_HOME", str(home))
    return home
