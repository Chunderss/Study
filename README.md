# Vocab Study

A desktop workspace for reading, source-linked questions and concepts, vocabulary, Markdown notes, and spaced repetition.

## Windows: launch without PowerShell

Extract **VocabStudy-Windows.zip**, open the extracted `VocabStudy` folder, and
double-click **VocabStudy.exe**. Keep `_internal` beside the executable; it contains
Python, Qt, the offline English WordNet dictionary, and NLTK's English part-of-speech
tagger. End users do not need Python, PowerShell, or an internet connection for
WordNet lookups.

To produce that ZIP from this source:

- On Windows, install **Python 3.12** with its Python Launcher, then double-click
  **build_windows.bat**. It installs build dependencies into `.venv-build`, downloads
  WordNet and the tagger, and creates `dist\VocabStudy-Windows.zip`. Building requires
  internet.
- Or run the repository's **Desktop tests and Windows executable** GitHub Actions
  workflow and download its `VocabStudy-Windows` artifact. It runs the tests, builds
  the ZIP, and smoke-tests the packaged executable with a temporary data directory.

Windows executables must be built on Windows. The source ZIP alone does not include
an already built Windows executable. The portable app is unsigned.

## Start studying

1. Click **+ New module**, then select the module.
2. Use **View → Notes**, **Vocab**, **Documents**, **Learning**, or **Console** to choose a pane.
3. Add a word in Vocab. `word :: your definition` supplies your own definition and
   replaces an existing entry; a dictionary lookup never replaces one. When the
   dictionary has several senses, a small panel lists them with the one studied in
   bold; press Ctrl+B, then `w`, to move into it, the arrow keys and Enter to study
   another sense, and Esc to close it. Click a word in the Vocab list (or move to it
   with Up and Down and press Enter) to see its senses later.
4. Click **Study** for this module, or **Study All** for all modules.
5. Use **View → Split side by side** to keep notes open alongside your book or vocabulary.

**F1** shows keyboard shortcuts. Existing Ctrl+B sequences still work: press
Ctrl+B, release it, then `v` to split, `n` to cycle components, `z` to zoom,
`s` to save a note, `r` to toggle its preview, or `w` for the senses of the last
added word. **Ctrl+S** also saves the focused note.

The bottom line shows the module, its due count and the dictionary. The line above it
shows the latest message on one line, such as a recovered draft, a workspace that
could not be restored, or a finished study session; hover over it for the full text.
When startup brings several notices, it shows the last one and how many more there
are. **View → Console** lists recent messages, including those from before it was
opened. Messages are kept in memory only, until the app closes.

## Read → capture → explain → revisit

1. Open a book from **Documents**. In EPUBs, highlight a passage and click **Capture**.
   In PDFs, click **Capture page**, then trim the extracted text to the relevant passage.
   Scanned PDFs without a text layer need a manually pasted excerpt; OCR is not included.
2. Write a question about the passage: why something happens, how concepts differ,
   an example, or how you would apply the idea. Choose **Concept to review** or
   **Open question to revisit**. Your own notes are optional.
   Saving an EPUB capture keeps the original passage highlighted when you reopen
   the book. Deleting the capture removes its highlight.
3. Open **Learning** from the sidebar or View menu. Open questions appear first;
   edit one to mark it resolved. You can also paste a passage using **+ Capture**.
   **Edit**, **Source** and **Delete** act on the highlighted capture, and Delete
   names it before removing it. With nothing highlighted, they are unavailable.

   To find captures, type in the search box above the list (**Ctrl+F** in a Learning
   pane). It shows the captures whose question, passage or notes contain every word you
   type, also inside longer words; case and accents on Latin letters do not matter
   (`cafe` finds "Café"). The menus beside it show one document, chapter or page, and
   open, resolved or all questions, or concepts due now, not due yet or all. **Esc**
   clears the search, **Down** or **Enter** moves to the list and highlights the first
   match, and **Clear filters** shows everything again. A highlighted capture that the
   search or filters hide is no longer highlighted, so the buttons never act on a
   capture you cannot see. The search and filters stay when you come back with
   **‹ Learning** and when the app reopens; selecting another module clears them. A new
   capture they hide is not listed: the summary counts the captures shown, and adding
   one here says it is hidden. **Review next due concept** always takes the module's
   next due concept.
4. Choose **Review next due concept**. Write an explanation from memory, then
   **Compare with source**. Rate yourself **Needs work** or **Understood** to save
   the attempt and schedule the next review. Closing an unfinished review leaves
   the schedule unchanged and asks before discarding an explanation. Starting a
   review clears the highlight, so no passage is shown while you recall.
5. Select a capture and click **Source** to return to its PDF page or highlighted
   EPUB passage. Captures made by older versions are found by their text, ignoring
   differences in line breaks, spacing and paragraph breaks; if that text appears
   more than once in the chapter, or not at all, they are not highlighted and Source
   opens at their saved position. Click **‹ Learning** to return to the same capture
   and list position. Links stay attached to the original module; if it was removed
   outside the app, the reader stays open and **View → Documents** leaves it.

Concept reviews are separate from vocabulary Study/Study All. Captures, explanations,
and review history are stored locally. This workflow requires no AI or network access.
EPUB highlights live with the module's captures; the original EPUB file is unchanged.
They also show in books whose pages forbid inline styles.
An optional tutor has not been added yet; no model is downloaded or sent your passages.

The EPUB reader's chapter list shows the titles from the book's table of contents
(its EPUB 3 navigation document, else its NCX), numbered in reading order; pages the
table of contents does not name show their file name. Long titles are shortened, with
the full title on hover.

The EPUB reader does not run a book's own JavaScript, because a script inside a
downloaded book could otherwise read files on your computer. Books render with their
own styles, images and fonts, and `<noscript>` fallback text is shown. Content that a
book builds with scripts, such as MathJax equations, is not rendered. Remote images,
stylesheets, fonts and frames are not loaded, and frames only show pages from the book.
This is not a full sandbox: a book can still point images at other files on your
computer, or ask the browser to open a connection to a server it names.

The reader unpacks a book into a temporary folder while it is open. It refuses a book
that would unpack to more than 4 GB, holds a single file larger than 1 GB, or holds
more than 20,000 files. It checks the sizes the book declares before writing any of
it, and stops unpacking if a file holds more than it declared. Such archives are
damaged or built to fill the disk; large image-heavy textbooks stay well within these
limits.

A book whose `container.xml` or package file is damaged still opens: the reader says
so above the page while the book is open, and View → Console keeps the message. If
the package file could not be read, chapters are listed by file name, in file-name
order, which may differ from the book's reading order. Pages that a book's reading
order gives as web addresses, or that are missing from the book, are skipped and
counted in the same notice.

The app reopens the selected module, pane layout, selected notes, preview visibility,
and document readers after a normal exit. A vocabulary quiz reopens as a Vocab pane;
its completed reviews are already saved. Each reader pane reopens at its own page
or chapter and zoom, also when two panes show the same document.

Opening a document that another pane already shows starts at that pane's place and
zoom (of several, the one used last); after that each pane moves on its own.
Otherwise a document starts where you last left a reader of it: going back,
switching or closing the pane saves that reader's place. When you quit with several
readers of a document open, the one you last scrolled, paged, zoomed or clicked in
counts; readers you have not used since the app started leave the place as it was.
**Source** opens at the capture, with the zoom of a pane already showing that
document, otherwise the saved zoom.

## Markdown notes and drafts

Notes have a plain-text Markdown editor and a local Qt preview. Headings, emphasis,
lists, tables, and fenced code update after a short pause. Narrow panes stack the
preview below the source. Tab moves between source and preview; use spaces for indentation.
While the preview is hidden it is not updated, so long notes stay quick to type in;
showing it renders the current text where you left it.

- Adding vocabulary or refreshing the workspace preserves text, cursor, selection, and undo.
- Drafts remain in memory when switching notes, modules, or components, or closing a pane.
  Reopen the note to continue. Two panes showing the same note share one document.
- While editing, the app writes a separate recovery copy about once a second. After
  an unexpected exit, drafts reappear in Notes with a **Recovered draft** indicator.
  Check the status beneath the editor: it reports pending backups and write failures.
  Edits since the last successful recovery write can still be lost in a crash.
- Closing the app offers **Save / Discard / Cancel** for all drafts, including hidden ones.
  Save updates the Markdown files; Discard explicitly removes the recovery copies.
  Save tries every draft. Notes that could not be saved, for example because another
  program changed them, are listed together and keep their drafts and recovery copies;
  the others are saved, and the app stays open. Previewing and recovery writes do not
  overwrite saved Markdown.
- Creating a note with an existing name is rejected. Deletion confirms that edits will be lost.
- If another program changes a file while you have a draft, saving reports a conflict
  instead of overwriting it. Copy your draft before resolving the external change.

Previewing never saves the note. Raw HTML and image resources are blocked, links don't
launch other apps, and LaTeX/Mermaid rendering is not provided.

## Dictionaries and study

The portable desktop build includes WordNet and the part-of-speech tagger, and reads
NLTK data only from its `_internal` folder: `nltk_data` folders and `NLTK_DATA` elsewhere
on the computer cannot replace them. Online Free Dictionary remains available. When a word is added from a sentence (from the EPUB
reader), sense selection keeps the senses of the word's part of speech in that sentence
and puts the one whose definition shares the most words with it first. The part of
speech is usually right; the chosen sense is a best guess, so check it. The message
after adding a word with several senses says whether its first sense was guessed from
the sentence or is simply the dictionary's first, and how many more senses were saved.
The word keeps the sentence it was added from (at most 500 characters around the word).
In the terminal and the Console, `SENSES word` lists the senses saved for a word, marks
the one studied with `*` and shows that sentence; `SENSES word --all` also lists the
senses of its dictionary that were not saved. `SENSE 3 word` studies sense 3 of that
list instead: it moves to the front, a sense from `--all` is saved, no sense is removed,
and the word keeps its progress; `SENSE 3 word --restart` also starts its reviews over,
as for a new word. `SENSE 3,1 word` studies several senses, in that order: revealing the
card shows them all, numbered, and a typed answer in mechanical mode passes when it
passes for any one of them. If the word changed in another window or program since it
was listed, nothing is changed and SENSE asks you to list it again. A word with your own
definition (`word :: definition`) has no senses to choose.

In the desktop app, adding a word with several senses from Vocab or with **Add word** in
an EPUB shows the sense picker in a corner of the workspace, away from the pane you are
typing or reading in. It does not take keyboard focus or resize anything: Esc closes it
from anywhere (before Esc does anything else), and Ctrl+B `w` moves into it, or reopens
it for the last word looked up. Each word in the Vocab list is a link to its senses, and
the senses it studies are listed under it. In the list, Up and Down move between the
words and Enter opens the highlighted one; one Tab leaves the list. The list keeps its
scroll position and the highlighted word when it updates or you come back to it. The
picker shows the module and dictionary, whether the first sense was chosen automatically
or by you, and the sentence. A sentence longer than three lines, or a reply longer than
two, is cut, and its tooltip shows all of it. The panel grows when it must, so its list
shows at least five senses wherever the workspace has room. Moving into it highlights
the first studied sense, and Enter there keeps every sense the word studies, marking
them as chosen by you. Once you move the highlight or click a sense, Enter (or a double
click) studies only the highlighted sense at once; Space checks several and
**Study checked** studies those, in list order.
Either keeps the word's progress unless you tick **Start its reviews over** first (it
unticks itself after each choice); choosing again undoes the choice. **All senses**
looks up the dictionary's other senses in the background; one is saved only if you
choose it. A list that changed in another window or program is reloaded with nothing
highlighted, and a choice made from an outdated list is refused, so Enter never acts on
a sense you did not pick. The Console uses `SENSES` and `SENSE`.

Vocabulary lookups from Vocab and the EPUB reader run in the background. Failed additions
keep the input for retrying; lookups stay attached to the module where they started.
The optional Ollama sense selector requires a separately installed local server.

The Console supports `DICT`, `DICT USE wordnet`, `DICT USE freedict`, `MODE MECH ON`,
`DISAMBIG nlp|ollama|base`, `SENSES word [--all]`, `SENSE n word`, `STATS`, `DOCTOR`, and
`HELP`. Source installations can use
`DICT INSTALL wordnet` (about 13 MB: WordNet and the tagger; about 1.5 MB if only the
tagger is missing). Downloads are explicit and may take a while; the terminal
interface's first run downloads both when WordNet is the configured dictionary but is
missing. A download waits at most 30 seconds for a network that does not answer; the
terminal's first-run download can be skipped with Ctrl-C and is tried again at the next
start. `DOCTOR` and `DISAMBIG` report a missing tagger. Console commands
other than the main Vocab/reader lookup path run synchronously.

Leitner schedules reviews across five boxes, 1, 3, 7, 16 and 35 calendar days apart.
Each recorded answer saves progress immediately. Study All writes each review to its
original module. Cards changed, deleted, or reviewed in another session are skipped with
a notice, preserving the newer progress.

Intervals count calendar days on this computer's clock. A card answered on Monday with a
3-day interval is due from the start of Thursday (local midnight), whatever time you
answered; a missed card is due again from the start of the next day, which after a
late-night session can be minutes away. The day always starts at midnight. A day is 23
or 25 hours long across a daylight-saving change, and where the clocks jump forward at
midnight the day starts at its first hour. Learning concepts follow the same rule. Each
due date is saved as the moment its day starts where you answered. After a change of time
zone, or of daylight-saving rules, a card is due from the start of the local day that
contains that moment: moving east keeps its date, moving west can bring it a day earlier,
never later. The app may notice a new time zone only after a restart. Progress saved by
earlier versions, due a whole number of 24-hour periods after the answer, is due from the
start of that day.

Outside mechanical mode, Space or Enter reveals the answer, J or Y records **Got it**,
F or N records **Missed it**, and Esc ends the session. These keys count alone or with
Shift, never with Ctrl, Alt, Cmd or the Windows key, so a reflex Ctrl+F or Ctrl+Y does
not record an answer. Keyboard focus stays on the card, so pressing Space or Enter
again never records an answer. To use a button from the keyboard, Tab to it and press
Space or Enter, again without Ctrl, Alt, Cmd or the Windows key; the mouse works too.
In the terminal, press Enter to reveal the definition, then answer `did you get it?
[y/N]`: `y` records **Got it**, Enter or `n` **Missed it**, and `q` stops without
recording the card. Any other reply asks again, as does a failed save, so the answer is
not lost.

In mechanical mode you type the definition and press Enter. The judge compares it with
the card's definition and suggests a verdict, shown with its score and the reference;
nothing is saved until you decide. Enter keeps the judge's verdict; J or Y records
**Got it** and F or N **Missed it**, whatever the judge suggested, and the button marked
Enter is the suggestion. As in reveal mode, Space and keys with Ctrl, Alt, Cmd or the
Windows key never record a verdict, and Tab or the mouse reach the buttons. Ending the
session before choosing records nothing for that card. The summary counts the cards
where you overruled the judge. Pressing Enter twice records the judge's verdict, as Enter
and **Continue** did before, so read the reference before the second Enter; and once an
answer is judged, J, Y, F and N record a verdict, so a key typed after an early Enter can
record one. In the terminal, answer `did you get it? [Y/n]` with Enter to keep the
judge's verdict, or `y` or `n`; `q` stops without recording the card, and a failed save
asks again.

The judge goes by wording, not meaning: it suggests **Got it** when the answer contains
at least half of the key terms of the card's definition. The feedback states the verdict
and names the key terms left out, as the definition writes them.
Regular English inflections match (make, makes, making; story, stories), irregular ones
do not (made). A key term negated differently from the definition earns no credit
("not permanent" for "permanent"). A negation said with another word is the same
negation ("can't forget" for "impossible to forget", "without color" for "deprived of
color"). A "not" applies to the next word only, so a reversed answer that keeps the
other key terms can still pass.
Accents on Latin letters are optional (cafe matches café, zolty żółty, oeuvre œuvre),
while marks in other scripts must match (е is not ё). Chinese and Japanese text is not
split into words, so only whole matching words count there.
Numbers are key terms only in a definition without other key terms ("1984"), so "a
hundred years" passes for "a period of 100 years"; 1,760 and 1760 are the same number.
A definition made only of short or common words ("an ox", "to go") is compared on those
words. A word studied with several senses (see `SENSE` above) passes when the answer
passes for any one of them. The reference then lists them all, numbered as revealing
does, and the score line names the sense its score and feedback come from, as in
`score 29% (sense 2) — ...`: the best-scoring sense the answer passes for, else the
best-scoring one. When a card's definition has no words at all, the judge makes no
suggestion and you choose: Enter does nothing in the study view and asks again in the
terminal.

## Your data

Data is separate from the executable:

- Windows: `%LOCALAPPDATA%\VocabStudy`
- Linux/macOS: `~/.vocab`
- Override with `VOCAB_HOME` or `--home DIR`.

```
<root>/
  config.json
  workspace.json
  modules/<name>/
    module.json
    vocab/words.json
    vocab/stats.json
    notes/*.md
    drafts/*.json
    documents/*
    reading.json
    learning.json
```

Legacy `lists/` data is backed up and migrated on startup. Replacing the application
folder does not replace your data. Interrupted migrations can be retried; conflicting
existing modules remain intact and are reported for recovery. Notes and JSON writes
use atomic replacement.
Errors in the packaged app appear in a dialog and are logged to `desktop.log` in the data folder.

Vocabulary sharing remains available through Console commands:
`EXPORT Book book.json [--stats]` and `IMPORT book.json as NewBook [--stats]`.
Without `--stats`, an import starts with fresh progress.
Windows paths work as typed (backslashes are ordinary characters). Put double quotes
around a name or path that contains spaces: `IMPORT "C:\Users\Pat Smith\book.json" as NewBook`.

**Changed in this version: relative paths start in your Documents folder.** A relative
path such as `book.json`, in the terminal and in the Console, now means a file in the
*folder for files*, wherever the program was started. It used to mean a file in the
folder the program was started in, which for VocabStudy.exe started from Explorer is the
application folder that a new build replaces. The folder for files is your Documents
folder unless you choose another: on Windows the Documents folder Explorer shows (it
follows OneDrive and folder redirection), on Linux the XDG documents folder, otherwise
`~/Documents`, or your home folder when there is none.

- `FOLDER` shows the folder; `FOLDER <full path>` chooses another, which must exist;
  `FOLDER DEFAULT` goes back to Documents. The choice is saved in `config.json` and used
  by the terminal and the desktop app. An older version ignores it and drops it the next
  time it saves a setting.
- `~/book.json` is in your home folder, and a full path is used exactly as typed. `..`
  in a relative path is resolved as written. Windows paths relative to a drive
  (`D:book.json`) or to the current drive's root (`\book.json`) are refused.
- If the chosen folder is not available (an unplugged drive) or the setting is damaged,
  commands that use a relative path refuse and say so; nothing falls back to another
  folder. Full paths still work.
- Every reply shows the full path of the file written or read. Files exported with a
  relative path before this change stay where they were written.

These vocabulary exports include each word's senses and the sentence it was added
from, but not captures or notes; a module backup does.

**An older version also drops word details when it saves.** For each word this version
keeps the sentence it was added from, whether you chose its sense, and how many senses it
studies (`SENSE 3,1`). An older version reads such a module, but the first time it saves
that module's vocabulary (adding or deleting a word, for example) it silently drops all
three from every word in the module: the words keep their senses, the sense order and
their progress, but study only their first sense again. Older versions use the same data
folder by default, so back up or export the module before opening your data with one, or
give the older version its own data folder with `--home DIR`.

### Back up and restore modules

In the desktop app, the **Modules** menu has **Back up current module…** (a Save
dialog that starts at the dated name in the folder for files), **Back up all
modules…** (choose the folder that gets the new dated folder), **Restore from
backup…** (choose one or more backup files; when a module name is taken it asks for
another, suggesting "Name (restored)"), and **Folder for files…**. Backing up asks first
when an open note has unsaved changes, since the backup holds the saved version. A
progress window with a **Cancel** button appears for longer operations; Cancel leaves
nothing behind. When several backups are restored, Cancel also stops the ones not yet
restored and the message says which were restored (cancelling the question for a new
name skips only that backup). The message line shows the result, and **View → Console**
lists every message. The same commands typed in the Console behave the same way.

`BACKUP <module> [<file.zip> | <folder>]`, in the terminal or the Console, writes one ZIP
archive with everything the module holds: its words and study progress, notes, documents,
Learning captures (with their source links, EPUB highlights and review attempts) and
reading positions.

- Without a destination the archive is `<Module> <YYYY-MM-DD>.zip` in the folder for
  files (` (2)`, ` (3)`... when that name is taken). A folder given gets that name
  inside it; a file name without `.zip` gets `.zip` appended. The folder must exist.
- An existing file is replaced only after you confirm.
- The module is checked first with the rules a restore applies, so every archive
  written can be restored. A module with a damaged file is refused, naming the file;
  repair or move it, or copy the module's folder yourself.
- The module is left exactly as it was. Its small files are read while other windows
  wait a moment; each document is copied afterwards and checked for changes, and a
  document that changed meanwhile refuses the backup (try again).
- The archive is written under a hidden temporary name beside its destination, read
  back and checked, and only then given its name, so a failure, a full disk or Ctrl-C
  leaves no file behind.
- Not included, and said in the reply: recovery copies of unsaved note changes (save
  them first) and files in the module's folder that Vocab Study does not manage.
- Large backups print a start line; Ctrl-C cancels.

`BACKUPALL [<folder>]` backs up every module at once into a new folder,
`Vocab Study backup <YYYY-MM-DD>` (` (2)`... when taken; an existing folder is never
reused), in the folder for files or in the folder given. It holds one `<Module>.zip` per
module, each exactly what `BACKUP` writes and restorable on its own. A module that cannot
be backed up, and a module folder whose name the app cannot use, is listed with the
reason without stopping the others. Ctrl-C stops after the current module; the archives
already written are complete. A folder left empty is removed.

`RESTORE <file.zip> [as <name>]` creates a NEW module from a backup, named as in the
backup unless `AS` gives a name (several words are fine). It never replaces or merges into
an existing module: when the name is taken, the reply shows the command to restore under
another name.

- Everything is checked before anything is written: that the file is a module backup
  (an `EXPORT` file is restored with `IMPORT`), not made by a newer version, not
  encrypted, with no links, no duplicate or unlisted files, no file outside the module
  folder, and no name the computer cannot use; the checksums; every JSON file's
  content, with the rules the app applies when it reads them; and free space.
- The module is built in a hidden folder in the data folder, every file checked against
  its checksum, and then put in place in one step. A failure, a full disk, Ctrl-C, or the
  name being taken meanwhile by another window leaves nothing behind, and an immediate
  retry works.
- The restored module keeps the backup's files byte for byte, so captures still open
  their source passage and EPUB highlights and reading positions return. Its
  `module.json` gets the new name and a new identity, so the app treats it as a
  different module from the original; its creation time is kept.
- A document or note name that the computer making the backup allowed but this one
  does not (such as `a:b.pdf`, made on Linux, restored on Windows) is refused; rename it
  where the backup was made.
- A backup holds at most 100,000 files; each JSON file or note at most 256 MB.
- Large restores print a start line; Ctrl-C cancels.

The archive is a standard ZIP file. A manifest, `vocab-study-backup.json`, records the
format (`vocab-study-module-backup`, version 1), the app version, the time, the module and,
for every file, its size and SHA-256; `module/` mirrors the module's folder. The
checksums detect damage, not deliberate changes, since anyone can rewrite the manifest
too, so a restore also checks the content of every JSON file.

## Development

Python 3.10+; Python 3.12 is used for Windows builds.

```bash
python -m pip install -e ".[gui,wordnet,test]"
python -m vocab.gui
python -m pytest -q
```

The optional terminal interface remains available as `python -m vocab` or `vocab`.
There, Ctrl-C cancels a running command and returns to the prompt; at the prompt it
leaves. A data folder that cannot be created or written is reported in one line.
GUI tests use Qt's offscreen platform and temporary data directories. Linux still
needs Qt's system libraries (including GL, EGL, and xkbcommon); EPUB rendering also
requires the Qt WebEngine runtime libraries.

The repository's workflow runs the whole test suite on Windows with Python 3.12 and the
current PySide6, then builds and smoke-tests the app. A second job runs it on Ubuntu
24.04 with Python 3.10, the oldest supported version, without the `gui` extra: the
terminal, storage and grading tests run there with real NLTK data, and the desktop tests
skip. Run the desktop tests on Linux locally.

Build on the target OS:

```bash
python -m pip install -e ".[desktop-build,test]"
python tools/build_desktop.py
```

The builder uses [PyInstaller](https://pyinstaller.org/en/stable/) in folder mode,
which bundles the Qt PDF/WebEngine helpers alongside the app. It downloads English
WordNet and NLTK's English part-of-speech tagger into `build/nltk_data`, checks that a
copy of exactly the bundled files works offline, and produces a portable ZIP under
`dist/`. The app ships only those two zips, not other NLTK data on the build machine.
`python tools/build_desktop.py --data-only` stops after the data check.
