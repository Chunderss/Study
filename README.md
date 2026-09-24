# Vocab Study

A desktop workspace for vocabulary, Markdown notes, PDF/EPUB reading, and spaced repetition.

## Windows: launch without PowerShell

Extract **VocabStudy-Windows.zip**, open the extracted `VocabStudy` folder, and
double-click **VocabStudy.exe**. Keep `_internal` beside the executable; it contains
Python, Qt, and the offline English WordNet dictionary. End users do not need
Python, PowerShell, or an internet connection for WordNet lookups.

To produce that ZIP from this source:

- On Windows, install **Python 3.12** with its Python Launcher, then double-click
  **build_windows.bat**. It installs build dependencies into `.venv-build`, downloads
  WordNet, and creates `dist\VocabStudy-Windows.zip`. Building requires internet.
- Or run the repository's **Desktop tests and Windows executable** GitHub Actions
  workflow and download its `VocabStudy-Windows` artifact. It runs the tests, builds
  the ZIP, and smoke-tests the packaged executable with a temporary data directory.

Windows executables must be built on Windows. The source ZIP alone does not include
an already built Windows executable. The portable app is unsigned.

## Start studying

1. Click **+ New module**, then select the module.
2. Use **View → Notes**, **Vocab**, **Documents**, or **Console** to choose a pane.
3. Add a word in Vocab. `word :: your definition` supplies your own definition.
4. Click **Study** for this module, or **Study All** for all modules.
5. Use **View → Split side by side** to keep notes open alongside your book or vocabulary.

**F1** shows keyboard shortcuts. Existing Ctrl+B sequences still work: press
Ctrl+B, release it, then `v` to split, `n` to cycle components, `z` to zoom,
`s` to save a note, or `r` to toggle its preview. **Ctrl+S** also saves the focused note.

## Markdown notes and drafts

Notes have a plain-text Markdown editor and a local Qt preview. Headings, emphasis,
lists, tables, and fenced code update after a short pause. Narrow panes stack the
preview below the source. Tab moves between source and preview; use spaces for indentation.

- Adding vocabulary or refreshing the workspace preserves text, cursor, selection, and undo.
- Drafts remain in memory when switching notes, modules, or components, or closing a pane.
  Reopen the note to continue. Two panes showing the same note share one document.
- Closing the app offers **Save / Discard / Cancel** for all drafts, including hidden ones.
  A failed save keeps the app open. Save regularly; in-memory drafts do not survive a crash.
- Creating a note with an existing name is rejected. Deletion confirms that edits will be lost.
- If another program changes a file while you have a draft, saving reports a conflict
  instead of overwriting it. Copy your draft before resolving the external change.

Previewing never saves the note. Raw HTML and image resources are blocked, links don't
launch other apps, and LaTeX/Mermaid rendering is not provided.

## Dictionaries and study

The portable desktop build includes WordNet. Online Free Dictionary remains available.
Vocabulary lookups from Vocab and the EPUB reader run in the background. Failed additions
keep the input for retrying; lookups stay attached to the module where they started.
The optional Ollama sense selector requires a separately installed local server.

The Console supports `DICT`, `DICT USE wordnet`, `DICT USE freedict`, `MODE MECH ON`,
`DISAMBIG nlp|ollama|base`, `STATS`, and `HELP`. Source installations can use
`DICT INSTALL wordnet`; downloads are explicit and may take a while. Console commands
other than the main Vocab/reader lookup path run synchronously.

Leitner schedules reviews across five boxes (1, 3, 7, 16, 35 days). Each answer saves
progress immediately. Mechanical mode keeps the reference answer and score visible
until **Continue**. Study All writes each review to its original module.

## Your data

Data is separate from the executable:

- Windows: `%LOCALAPPDATA%\VocabStudy`
- Linux/macOS: `~/.vocab`
- Override with `VOCAB_HOME` or `--home DIR`.

```
<root>/
  config.json
  modules/<name>/
    module.json
    vocab/words.json
    vocab/stats.json
    notes/*.md
    documents/*
    reading.json
```

Legacy `lists/` data is backed up and migrated on startup. Replacing the application
folder does not replace your data. Notes and JSON writes use atomic replacement.
Errors in the packaged app appear in a dialog and are logged to `desktop.log` in the data folder.

Vocabulary sharing remains available through Console commands:
`EXPORT Book book.json [--stats]` and `IMPORT book.json as NewBook [--stats]`.
Without `--stats`, an import starts with fresh progress.

## Development

Python 3.10+; Python 3.12 is used for Windows builds.

```bash
python -m pip install -e ".[gui,wordnet,test]"
python -m vocab.gui
python -m pytest -q
```

The optional terminal interface remains available as `python -m vocab` or `vocab`.
GUI tests use Qt's offscreen platform and temporary data directories. Linux still
needs Qt's system libraries (including GL, EGL, and xkbcommon); EPUB rendering also
requires the Qt WebEngine runtime libraries.

Build on the target OS:

```bash
python -m pip install -e ".[desktop-build,test]"
python tools/build_desktop.py
```

The builder uses [PyInstaller](https://pyinstaller.org/en/stable/) in folder mode,
which bundles the Qt PDF/WebEngine helpers alongside the app. It downloads English
WordNet data into `build/nltk_data` and produces a portable ZIP under `dist/`.
