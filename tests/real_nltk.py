"""Real NLTK data for tests: skipped where it is not installed, required in CI.

CI downloads the packages the desktop app bundles and sets
VOCAB_REQUIRE_NLTK_DATA=1, so these tests cannot silently stop running there.
"""
import os
from pathlib import Path

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
