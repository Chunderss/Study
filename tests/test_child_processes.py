"""Child Pythons that tests start import this checkout, whichever directory
pytest was started from."""
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LAUNCHERS = {"run", "Popen", "call", "check_call", "check_output"}


def test_a_child_started_outside_the_checkout_imports_it(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text("import vocab.core.paths as p\nprint(p.__file__)\n", encoding="utf-8")
    # -S leaves out site-packages, so no installed or editable copy of vocab
    # can stand in for this checkout: only the inherited environment finds it.
    result = subprocess.run([sys.executable, "-S", str(probe)], cwd=tmp_path,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == ROOT / "vocab" / "core" / "paths.py"


def _process_launches(tree):
    """Calls that start a process through the subprocess module, also under
    an alias (import subprocess as sp) or a direct import (from subprocess
    import run)."""
    modules, functions = {"subprocess"}, set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.asname or alias.name for alias in node.names
                           if alias.name == "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            functions.update(alias.asname or alias.name for alias in node.names
                             if alias.name in LAUNCHERS)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if ((isinstance(func, ast.Attribute) and func.attr in LAUNCHERS
                and isinstance(func.value, ast.Name) and func.value.id in modules)
                or (isinstance(func, ast.Name) and func.id in functions)):
            yield node


def test_every_child_process_has_an_explicit_working_directory():
    # Without cwd= a child runs in pytest's launch directory, which "python -c"
    # puts ahead of PYTHONPATH: another checkout's vocab there would win.
    # Keyword arguments passed as **kwargs cannot be checked, so they need an
    # explicit cwd= too.
    missing = []
    for path in sorted((ROOT / "tests").glob("*.py")):
        for call in _process_launches(ast.parse(path.read_text(encoding="utf-8"))):
            if not any(keyword.arg == "cwd" for keyword in call.keywords):
                missing.append(f"{path.name}:{call.lineno}")
    assert not missing, f"process launches without an explicit cwd=: {missing}"
