"""The GUI checks in tools/ keep passing, so they cannot quietly rot."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
pytest.importorskip("PySide6")

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("tool, verdict", [
    ("gui_viewers", "VIEWERS PASSED"),
    ("gui_disambig", "DISAMBIGUATION + HIGHLIGHT-ADD PASSED"),
])
def test_gui_check_passes(tmp_path, tool, verdict):
    # Temporary folders the tool makes land in this test's folder.
    env = dict(os.environ, TMPDIR=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path))
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    if sys.platform == "win32":
        env["QT_QPA_PLATFORM"] = "windows"  # the checks open QtWebEngine readers
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    result = subprocess.run([sys.executable, "-X", "faulthandler", str(ROOT / "tools" / f"{tool}.py")],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert verdict in result.stdout, output
    if os.environ.get("VOCAB_REQUIRE_NLTK_DATA") == "1":
        assert "[skip]" not in result.stdout, output  # CI has WordNet and the tagger
