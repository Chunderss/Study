# Vocab Study

A terminal-first, command-driven vocabulary trainer. Group words into **lists**
(e.g. *The Great Gatsby*), pull definitions from a swappable **dictionary**, and
review them with a swappable **spaced-repetition algorithm** (Leitner to start).
Designed to be low-friction and mostly keyboard-only, so studying takes little
thought to operate.

## Install

Requires Python 3.9+.

### Easiest: one-time setup script (recommended)

This creates the virtualenv, installs everything, downloads the offline
dictionary, and verifies it — so you never have to worry about "which Python."

```powershell
# Windows PowerShell, from the project folder:
.\setup.ps1
# if PowerShell blocks scripts:  powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

```bash
# macOS / Linux / git-bash:
bash setup.sh
```

Then start it with the launcher (always uses the correct Python):

```
.\vocab.ps1        # PowerShell
vocab.bat          # Command Prompt (or double-click it)
bash vocab.sh      # macOS / Linux / git-bash
```

### Manual install (if you prefer)

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows (git-bash)
# or:  python3 -m venv .venv && source .venv/bin/activate  # macOS/Linux
pip install -e ".[wordnet]"     # program + offline WordNet dictionary
```

> **Important — use the venv's Python.** If you run `python -m vocab` with a
> *different* Python (e.g. a system Python 3.14 on your PATH) that doesn't have
> `nltk` installed, the offline dictionary is unavailable and lookups silently
> fall back to an online service that can time out. The launcher scripts avoid
> this by always calling `.venv\Scripts\python.exe`. If in doubt, run `DOCTOR`
> inside the program — it tells you exactly what's wrong and how to fix it.

## Run

```bash
python -m vocab              # start the REPL (use the venv Python!)
python -m vocab --home DIR   # use a specific data directory
```

On first run, if `nltk` is installed but the WordNet corpus isn't downloaded
yet, the program fetches it automatically (~40 MB, one time). Type `HELP` for
the full command reference, or `DOCTOR` to check your setup.

### 60-second start

```
CREATE Gatsby
USE Gatsby
ADD ephemeral          # looks the word up and adds it to the current list
STUDY                  # review what's due (Leitner)
```

Multi-word list names use quotes: `CREATE "The Great Gatsby"`.

## Live Markdown notes (desktop)

The Notes component shows editable Markdown and a live formatted preview.
Headings, emphasis, lists, fenced code and tables update shortly after typing;
previewing does **not** save or rewrite the Markdown file.

- **Ctrl+B, release, then r**: show/hide the focused note's preview (or use Preview).
- **Tab / Shift+Tab**: move between the source editor and preview; arrow/PageDown
  keys scroll the preview. Use spaces for indentation in the editor.
- **Ctrl+B, release, then s**: save the note. F1 lists the keyboard shortcuts.
- Wide Notes panes show source and preview side by side. Narrow panes stack them.

Rendering is local using Qt, not Ollama: no model calls, downloads or build step.
This is a Markdown text preview, not an HTML/browser runtime: raw HTML is ignored,
external images/resources are not loaded, and links do not launch other apps.
LaTeX math and Mermaid rendering are not included.

## Dictionaries (many installed, one active)

| id         | source                         | offline | install cost |
|------------|--------------------------------|:-------:|--------------|
| `wordnet`  | WordNet via NLTK **(default)** |   yes   | ~40 MB corpus |
| `freedict` | dictionaryapi.dev (online API) |   no    | none         |

- **WordNet is the default.** If its corpus isn't installed yet, the program
  **falls back to `freedict`** automatically and tells you how to enable the
  offline default — you're never stuck.
- Install / download the corpus from inside the program (it confirms the size
  first):

  ```
  DICT                     # list dictionaries, sizes, and status
  DICT INSTALL wordnet     # download the ~40 MB corpus
  DICT USE wordnet         # make it active
  ```

- Add your own definition without any dictionary:
  `ADD serendipity :: a happy accident`
- Downloadable StarDict/`.dict` files are on the roadmap (the `Dictionary`
  interface already supports adding new backends).

> **Note on WordNet sense order:** WordNet returns senses ordered by corpus
> frequency, so the first sense isn't always the "textbook" one (e.g. *ephemeral*
> returns the noun "a mayfly" before the adjective "lasting a very short time").
> All senses are stored; the first is shown as the primary. Sense selection is a
> planned refinement.

## Studying

```
STUDY                # current list
STUDY <list>         # a specific list
STUDYALL             # due cards mixed across ALL lists
STATS [list]         # box/progress per word
```

**STUDYALL** is an *ephemeral* mixed session — it pulls due cards from every
list and writes each word's progress **back to its home list**. There is no
separate "mixed" list and no divergent progress.

### Study modes

- **Reveal (default):** see the word, recall silently, reveal the definition,
  self-grade `y/N`.
- **Mechanical repetition:** `MODE MECH ON` — you *type* the definition and a
  local judge scores how closely it matches. The MVP judge is deterministic
  keyword/lemma overlap (no downloads, no model); ≥ 50% overlap counts as
  correct. A semantic judge (embeddings / local LLM) is a planned drop-in behind
  the same interface.

## Spaced repetition (Leitner)

Cards live in boxes 1–5 with review intervals of **1, 3, 7, 16, 35 days**. A
correct answer promotes a card one box; a wrong answer sends it back to box 1.
The algorithm is pluggable (`vocab/algorithms/`), so SM-2 / FSRS can be added
without touching the study loop.

## Sharing lists

Lists are plain JSON, so they're easy to share.

```
EXPORT Gatsby gatsby.json            # words only
EXPORT Gatsby gatsby.json --stats    # words + your progress
IMPORT gatsby.json as Gatsby2        # import; start your OWN fresh progress
IMPORT gatsby.json as Gatsby2 --stats  # also import the shared progress
```

By default an import gives you the words with **fresh** progress — you learn at
your own pace.

## Data layout

Everything lives under one root (`%LOCALAPPDATA%\VocabStudy` on Windows,
`~/.vocab` elsewhere; override with `--home` or `$VOCAB_HOME`):

```
<root>/
  config.json                 active dictionary / algorithm / judge / mode
  dictionaries/               installed dictionary data (e.g. .dict files)
  lists/
    The Great Gatsby/
      words.json              the importable word/definition payload
      stats.json              per-word scheduler state (importable progress)
      sources/                PDF/EPUB of the book (reference; not parsed yet)
```

### `words.json` schema

```json
{
  "schema": 1,
  "name": "The Great Gatsby",
  "words": {
    "ephemeral": {
      "dictionary": "wordnet",
      "added": "2026-09-01T12:00:00+00:00",
      "senses": [
        { "definition": "lasting a very short time", "pos": "adjective", "example": "" }
      ]
    }
  }
}
```

## Command reference

| Command | Purpose |
|---|---|
| `CREATE <name>` | create a list |
| `DELETE <name>` | delete a list (and its progress) |
| `LISTS` | show all lists (`*` = current) |
| `USE <name>` / `CD <name>` | set the current list |
| `WHERE` / `PWD` | show the current list |
| `LS [name]` | list words in a list |
| `ADD <word>` | add to current list (dictionary lookup) |
| `ADD <word> :: <def>` | add with your own definition |
| `ADDL <list> <word>` | add to a specific list (`::` supported) |
| `DEL <word>` / `DELL <list> <word>` | remove a word |
| `DICT` / `DICT USE <id>` / `DICT INSTALL <id>` | manage dictionaries |
| `STUDY` / `STUDY <list>` / `STUDYALL` | study sessions |
| `STATS [list]` | progress per word |
| `MODE` / `MODE MECH ON\|OFF` | study mode |
| `ALGO` | show active algorithm |
| `EXPORT` / `IMPORT` | share lists |
| `DOCTOR` | diagnose setup (Python, dictionary, offline status) |
| `HELP` / `QUIT` | help / exit |

## Architecture

Layered so features drop in without touching the core:

```
vocab/
  core/         data models, directory layout, JSON + stats persistence, config
  dictionaries/ Dictionary interface + backends (freedict, wordnet) + registry
  algorithms/   Scheduler interface + implementations (leitner)
  study/        study-session loop + pluggable answer Judge
  cli/          REPL, command parser, dispatch table, help text
```

The three extension seams — `Dictionary`, `Scheduler`, `Judge` — are formal
interfaces; adding a backend is one new file plus one registry line.

## Tests

```bash
python -m unittest tests.test_vocab -v
```

21 tests cover storage round-trips, Leitner promotion/demotion, the parser,
the judge, REPL dispatch, dictionary default/fallback, and (when the corpus is
installed) live offline WordNet lookups. Tests are hermetic — they use a temp
data directory and a mock dictionary, so they never touch the network or your
real data.

## Roadmap

- Semantic judge for mechanical-repetition mode (embeddings or local LLM).
- Downloadable StarDict/`.dict` dictionary backend.
- Reading PDF/EPUB sources in-app (currently stored for reference only).
- Additional algorithms (SM-2 / FSRS).
- Smarter WordNet sense selection.
