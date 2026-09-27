# Repository Guidelines

## Project Structure & Module Organization

Run the commands below from the repository root. Keep application code, regression tests, and packaging changes together when a fix affects the desktop build.

The application is Vocab Study, a Python desktop study workspace. Within the checkout:

- `vocab/core/`: storage, configuration, migrations, and learning records.
- `vocab/gui/`: PySide6 windows, Markdown editing, PDF/EPUB readers, and shortcuts.
- `vocab/cli/`: terminal commands and REPL.
- `vocab/algorithms/`, `vocab/study/`, `vocab/dictionaries/`: scheduling, answer evaluation, and dictionary providers.
- `tests/`: automated regressions; `tools/`: GUI checks and build scripts.
- `packaging/desktop.py` and `VocabStudy.spec`: desktop packaging. Generated assets and bundles belong in `build/` and `dist/`.

## Build, Test, and Development Commands

Use Python 3.10+; Windows CI uses Python 3.12. Create and activate a virtual environment before installing dependencies.

- `python -m pip install -e ".[gui,wordnet,test]"`: install editable source and development dependencies.
- `python -m vocab.gui`: launch the desktop application.
- `python -m vocab`: launch the terminal interface.
- `python -m pytest -q`: run the test suite.
- `python -m pip install -e ".[desktop-build,test]"`, then `python tools/build_desktop.py`: build a portable ZIP under `dist/` on the target OS. Windows also supports `build_windows.bat`.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` for functions/modules, `PascalCase` for classes, and uppercase constants. Match nearby code and keep storage logic separate from GUI behavior. No formatter or linter is configured; avoid unrelated formatting changes.

## Testing Guidelines

Tests use pytest, `test_*.py` filenames, and `test_*` functions. Add focused regressions for changed behavior, especially persistence, draft recovery, and reader interactions. Use `tmp_path` for data isolation and Qt's offscreen platform for GUI tests. Install PySide6 to avoid skipped GUI coverage; Linux also needs Qt system libraries. CI downloads WordNet and tests the packaged Windows executable. No numeric coverage threshold is configured.

## Commit & Pull Request Guidelines

History uses imperative subjects such as “Restore Ctrl+B shortcuts for native window key events”; follow this style. Keep commits focused. PRs should describe the problem, resulting behavior, and validation, link relevant issues, and include screenshots for visible GUI changes. Ensure desktop CI passes.

## Data & Configuration

Use `VOCAB_HOME` or `--home DIR` for isolated development data. Preserve atomic writes and migration backups. Keep personal notes, dictionaries, virtual environments, and generated bundles out of commits.
