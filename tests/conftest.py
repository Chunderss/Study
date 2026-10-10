"""Settings every test gets, which child processes inherit through os.environ.

Every child Python a test starts must import this checkout, whichever
directory pytest was started from. vocab is a namespace package, so a child
otherwise imports the vocab/ its working directory or an installed copy of
another checkout provides.
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
