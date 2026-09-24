# Reliability review and desktop packaging

The original archive is preserved beside the updated source folder.

## Notes and keyboard input

- Notes now use a plain-text editor; formatting shortcuts cannot alter source formatting.
- Application shortcuts consume their key events and ignore held-key auto-repeats.
  They are scoped to the main window and do not hijack input in dialogs.
- Removed a lambda signal connection that could retain replaced components.
- Workspace refreshes keep the current document, cursor, selection, and undo history.
- Shared buffers keep drafts across note/module switches and pane replacement/closure.
  Two panes editing one note share the same document rather than overwriting each other.
- Save uses the loaded note's module identity, even if the current module has changed.
- Closing the application handles all unsaved drafts with Save/Discard/Cancel.
  Failed saves leave the window and drafts intact.
- Note creation is exclusive; duplicate names cannot overwrite an existing note.
- Deleting a note clears source and preview across open views. Explicit module/note
  deletions also discard the corresponding drafts.
- Concurrent external edits are detected before saving a draft.
- Preview rendering preserves scroll during edits and resets it for a different note.
- Corrected narrow-pane layout constraints and stopped resize events from continually
  resetting the user's note-picker split position.
- Added desktop menus and Ctrl+S alongside the existing leader shortcuts.

## Vocabulary, study, and documents

- Vocabulary and reader lookups run in a worker thread; writes occur on the GUI thread
  against fresh storage state and the original target module.
- Failed lookups keep the entry text. Late completions do not erase newly typed input.
- Empty definitions and empty dictionary responses are rejected before writing.
- Removed startup dictionary downloads, implicit NLP resource downloads, and Ollama
  network checks during focus changes.
- Fixed duplicate senses when the inferred part of speech matches none of the senses.
- Study grades persist immediately, surviving pane closure or early application exit.
  Finishing a session no longer replays old stats over newer/deleted cards.
- Mechanical study keeps feedback visible until Continue. Its Enter event is consumed
  so the same press cannot submit and immediately advance again.
- Completion locates the originating study pane instead of replacing the focused pane.
- Study without a selected module no longer silently starts Study All.
- The desktop Console uses Qt confirmation dialogs instead of terminal input and renders
  output as plain text. Console module changes update the sidebar and workspace.
- Duplicate document names are rejected rather than silently replaced.
- PDF reading position is restored even when the load completes synchronously; invalid
  PDFs surface an error, zoom is bounded, and malformed saved page numbers are tolerated.
- Closing a pane now runs document state-saving hooks. EPUB scroll saving reads Qt's
  cached position synchronously rather than relying on JavaScript after shutdown.
- EPUB paths are confined to the extracted book, chapter fragments are handled, and
  temporary extraction directories are cleaned up when their book objects are released.
- Reader vocabulary additions retain the book's original module. Late reading-position
  callbacks cannot recreate a deleted module.

## Storage and distribution

- Clean up temporary note/config files after failed atomic writes.
- Reject Windows device names and trailing dots; retain legitimate note names such as `notes`.
- Respect filesystem case rules when detecting existing modules, preventing Windows
  case variants from overwriting one another.
- Treat the module directory as authoritative if a JSON payload contains another name.
- Restore the missing terminal entry point advertised by the package metadata.
- Add a windowed PyInstaller specification, a double-click Windows build script, and a
  Windows CI workflow with regression tests, executable smoke test, and ZIP artifact.
- Bundle the English WordNet corpus; no download is required for packaged offline lookups.
- Report desktop exceptions in a dialog and log them under the user's data directory.

## Verification and limits

Validated here with Python 3.10.12, PySide6 6.11.2, NLTK 3.10.3, and PyInstaller 6.22.3:

- **80 tests passed**, including real Qt key delivery, IME commits, shared drafts, save
  failures, asynchronous lookups, storage, study progress, and PDF/EPUB parsing tests.
- Built and launched the Linux bundle in Qt offscreen mode using local system-library
  dependencies. Its smoke test verified the bundled offline dictionary, Markdown rendering,
  and Qt PDF/WebEngine imports. A normal Linux desktop must supply Qt's system libraries.
- Visually checked a rendered note with headings, bold/italic text, lists, tables, and code.
- Verified the restored terminal entry point accepts `--help`.

**No Windows executable was built or run in this Linux workspace.** Use `build_windows.bat`
on Windows (Python 3.12 required for building) or the included GitHub Actions workflow.
The workflow has been added but was not run remotely. The exact intermittent Windows
keystroke symptom could not be reproduced on this platform; input handling is hardened
and covered by synthetic Qt key and IME regression tests.

Drafts remain in memory until saved; they do not survive a process crash. Console commands
other than the main Vocab/reader lookup paths remain synchronous. Full interactive EPUB
browser behavior, native Windows IME behavior, and Windows display scaling still need a
Windows desktop smoke test. This review does not establish that every possible bug is gone.
