"""Real NLTK data for tests: skipped where it is not installed, required in CI.

CI downloads the packages the desktop app bundles and sets
VOCAB_REQUIRE_NLTK_DATA=1, so these tests cannot silently stop running there.
"""
import os
from pathlib import Path
import shutil

import pytest


def require(resource):
    """Path of an installed NLTK resource file such as 'corpora/wordnet.zip'."""
    nltk = pytest.importorskip("nltk")
    try:
        return Path(str(nltk.data.find(resource)))
    except LookupError:
        message = f"NLTK resource {resource!r} is not installed (set NLTK_DATA)"
        if os.environ.get("VOCAB_REQUIRE_NLTK_DATA") == "1":
            pytest.fail(message)
        pytest.skip(message)


def bundle_copy(folder, packages=None):
    """A data folder holding exactly the zips the desktop build bundles
    (or only those of the given packages)."""
    from vocab.nltk_resources import NLTK_PACKAGES
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    for package, bundled in NLTK_PACKAGES.items():
        if packages is None or package in packages:
            target = folder / bundled
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(require(bundled), target)
    return folder


def isolated_env(home):
    """Environment for a child interpreter that sees no user or system NLTK data."""
    env = {k: v for k, v in os.environ.items() if k != "NLTK_DATA"}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home),
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return env
