"""Help text — the getting-started guide a new user reads first."""

BANNER = r"""
 __     __            _
 \ \   / /__  ___ __ _| |__      Vocab Study  v0.1
  \ \ / / _ \/ __/ _` | '_ \     your terminal study workspace
   \ V / (_) | (_| (_| | |_) |
    \_/ \___/ \___\__,_|_.__/    type HELP for commands, QUIT to exit
"""

HELP = """\
VOCAB STUDY — command reference
================================
Everything lives in a MODULE (e.g. "The Great Gatsby") — a study container that
holds a vocab list, markdown notes, and reference documents. Pick a current
module with USE, then ADD words or write NOTES. STUDY runs spaced repetition
(Leitner) over the module's vocab.  (LIST/LISTS still work as aliases.)

GETTING STARTED (first 60 seconds)
  CREATE Gatsby              make a module called "Gatsby"
  USE Gatsby                 make it your current module
  ADD ephemeral              look the word up + add it to the module's vocab
  STUDY                      study what's due in the current module

MODULES & NAVIGATION
  CREATE <name>              create a new module
  DELETE <name>              delete a module and ALL its contents permanently
  MODULES                    show all modules (word counts; * = current)
  USE <name>                 set the current module  (alias: CD <name>)
  WHERE                      show the current module  (alias: PWD)
  LS [name]                  list words in a module (defaults to current)
      Multi-word names use quotes:  CREATE "The Great Gatsby"

VOCAB
  ADD <word>                 add <word> to the current module
  ADD <word> :: <definition> add with your OWN definition (skips the dictionary)
  ADDL <module> <word>       add <word> to a specific module
  DEL <word>                 remove <word> from the current module
  DELL <module> <word>       remove <word> from a specific module

NOTES  (markdown; edit bodies comfortably in the desktop app)
  NOTE                       list notes in the current module
  NOTE VIEW <name>           print a note
  NOTE ADD <name> [:: text]  create/replace a note (omit text, then edit in GUI)
  NOTE DEL <name>            delete a note

DICTIONARIES  (many installed, one active)
  DICT                       list dictionaries (shows install size + status)
  DICT USE <id>              set the active dictionary (e.g. freedict, wordnet)
  DICT INSTALL <id>          download/prepare a dictionary (asks before large DLs)

STUDYING
  STUDY                      study due cards in the current module
  STUDY <module>             study due cards in a specific module
  STUDYALL                   study due cards mixed across ALL modules
                             (progress is written back to each word's home module)
  STATS [module]             show box/progress for each word

MODES & ALGORITHM
  MODE                       show study mode (reveal vs. write-the-definition)
  MODE MECH ON|OFF           toggle mechanical repetition (type the definition;
                             a local judge scores how well it matches)
  ALGO                       show the active spaced-repetition algorithm
  DISAMBIG                   show how the right definition is picked from context
  DISAMBIG nlp|ollama|base   switch sense-selection engine (ollama = local LLM)

SHARING
  EXPORT <module> <file.json> [--stats]   write a portable vocab file
  IMPORT <file.json> [as <name>] [--stats]   import vocab as a new module
      Without --stats you get the words but start your OWN fresh progress.

OTHER
  DOCTOR                     diagnose setup (Python, dictionary, offline status)
  HELP                       show this help
  QUIT / EXIT                leave the program
"""
