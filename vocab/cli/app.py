"""Application state + command handlers.

App is UI-agnostic: every handler returns a string (what to print) and never
calls input()/print() directly, EXCEPT the interactive STUDY loop which needs a
prompt callback. This keeps the whole thing testable — repl.py wires real stdin/
stdout, tests drive App methods directly.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, List, NamedTuple, Optional

from ..algorithms.base import get_scheduler
from ..core.config import Config
from ..core import backup as _backup
from ..core import migrate as _migrate
from ..core.models import Sense, Word
from ..core import paths as _paths
from ..core.paths import expand_home, get_paths, resolve_user_path, sanitize_list_name
from ..core.senses import (LookedUp, SenseListing, parse_sense_numbers, quoted_sentence,
                           reorder, saved_context)
from ..core.locking import write_lock
from ..core.storage import ListNotFound, Storage, full_path, in_transaction, size_text
from ..dictionaries import LookupFailed, describe_all, get_dictionary
from ..dictionaries.base import SENSE_LIMIT
from .. import nltk_resources
from ..study.judge import get_judge
from ..study.session import StudyCardUnavailable, StudySession
from .parser import BODY_SEPARATOR, CommandSyntaxError, quote_arg

# Callbacks the REPL supplies so STUDY can be interactive while staying testable.
Prompt = Callable[[str], str]        # prompt(text) -> user input line
Echo = Callable[[str], None]         # echo(text) -> None (print)
_STOP = object()                     # the user stopped the study session at a prompt


# A packaged app has no pip or virtualenv to repair, and its bundled data is
# not installed separately: it is replaced whole.
PACKAGED_REPAIR = ("Extract a fresh copy of the Vocab Study ZIP and keep its _internal "
                   "folder beside the VocabStudy program.")


def _packaged() -> bool:
    """True in the desktop app built by PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def _typed_word(word: str, command: bool = True) -> str:
    """``word`` as the user would type it again, or "" when it cannot be typed.

    Commands (the terminal and the desktop Console) use the shared grammar, so
    the word is double-quoted when it needs it. The Vocab pane takes its text
    as typed, up to the first '::', so there a word with '::' has no form.
    """
    if not command:
        return "" if BODY_SEPARATOR in word else word
    try:
        return quote_arg(word)
    except CommandSyntaxError:
        return ""


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _quoted_names(names) -> str:
    return ", ".join(f"'{name}'" for name in names)


def backup_counts(summary: dict) -> str:
    """'42 words, 3 notes, 2 documents, 15 captures'."""
    return ", ".join(_count(summary.get(key, 0), key[:-1])
                     for key in ("words", "notes", "documents", "captures"))


def backup_reply(result) -> str:
    """The reply to a finished backup, with what it left out."""
    lines = [f"Backed up '{result.module}' -> {result.path} ({backup_counts(result.summary)}; "
             f"{size_text(result.size)})."]
    if result.drafts:
        which = "the note" if len(result.drafts) == 1 else "the notes"
        lines.append(f"  Not included: recovery copies of unsaved changes to {which} "
                     f"{_quoted_names(result.drafts)}. Save them in the desktop app, then back "
                     "up again.")
    if result.others:
        items = _count(len(result.others), "other item")
        lines.append(f"  Not included: {items} in {result.folder} that Vocab Study does not "
                     "manage.")
    return "\n".join(lines)


def _tagger_fix(problem: nltk_resources.TaggerProblem) -> str:
    """The command that makes part-of-speech tagging work, or ''."""
    if _packaged():
        return PACKAGED_REPAIR  # the bundle includes nltk and the tagger
    if problem.kind == "no-nltk":
        return 'pip install "vocab-study[wordnet]"'
    if problem.kind == "no-data":
        return "dict install wordnet"
    return ""  # the data is there but unreadable: the reason names the cause


class WordAdded(NamedTuple):
    """What committing a looked-up word did."""
    message: str
    added: bool     # the entry was written; False when an existing one was kept
    module: str     # the module's stored name
    word: str       # the word's stored spelling


class App:
    def __init__(self, root: Optional[str] = None):
        self.paths = get_paths(root)
        # One-time migration from the legacy lists/ layout to modules/.
        self.migration_note = ""
        self.migration_problem = ""  # kept visible until the migration succeeds
        try:
            created = []
            if _migrate.needs_migration(self.paths):
                with write_lock(self.paths.root).hold():  # one process migrates at a time
                    if _migrate.needs_migration(self.paths):
                        created = _migrate.migrate(self.paths)
            if created:
                self.migration_note = (
                    f"Migrated {len(created)} list(s) to the new Module format "
                    f"(a backup was saved alongside): {', '.join(created)}")
        except _migrate.MigrationIncomplete as e:
            self.migration_note = f"Legacy lists were not fully migrated: {e}"
            self.migration_problem = str(e)
        except Exception as e:  # never block startup on migration
            self.migration_note = f"(migration skipped: {e})"
            self.migration_problem = str(e)
        self.storage = Storage(self.paths)
        try:
            self.storage.purge_leftovers()
        except Exception:
            pass  # never block startup on cleanup
        self.config = Config.load(self.paths)
        # current_module is the active module; current_list kept as an alias.
        self.current_module: Optional[str] = None
        # The latest SENSES list per (module, word key), which SENSE numbers name.
        self._sense_listings = {}

    # ---- helpers --------------------------------------------------------
    @property
    def current_list(self) -> Optional[str]:
        """Backwards-compatible alias for current_module (a module's vocab was
        formerly a 'list'). Reads and writes both stay in sync."""
        return self.current_module

    @current_list.setter
    def current_list(self, value: Optional[str]) -> None:
        self.current_module = value

    @property
    def scheduler(self):
        return get_scheduler(self.config.active_algorithm)

    def effective_dictionary(self):
        """Return (dictionary, notice). Uses the configured dictionary if it is
        available; otherwise falls back to the first available backend so a
        fresh user (no WordNet corpus yet) can still add words. The notice is a
        one-line string (or "") the caller may surface once."""
        from ..dictionaries import REGISTRY, get_dictionary
        preferred = get_dictionary(self.config.active_dictionary)
        if preferred.available():
            return preferred, ""
        for dic in REGISTRY.values():
            if dic.available():
                notice = (
                    f"(note: '{preferred.id}' is not installed — using '{dic.id}' "
                    f"for now. Enable the offline default with:  dict install {preferred.id})"
                )
                return dic, notice
        # nothing available at all — return preferred so its error surfaces
        return preferred, ""

    def cmd_doctor(self) -> str:
        """Health check: surfaces the exact reason lookups might be slow/failing.

        The classic failure is running under a Python that lacks nltk, which
        makes WordNet unavailable and silently falls back to the online
        dictionary (which then times out). This makes that visible in one call.
        """
        from ..dictionaries import REGISTRY, get_dictionary

        lines = ["Vocab Study — doctor", "=" * 40]
        # 1. interpreter: facts only. A packaged app is never a virtualenv, and
        # a source install need not be one either.
        packaged = _packaged()
        lines.append(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
        if packaged:
            lines.append("Runtime     : packaged app")
        else:
            in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
            lines.append(f"Virtualenv  : {'yes' if in_venv else 'no'}")

        # 2. nltk + corpus
        try:
            import nltk  # noqa: F401
            nltk_ok = True
            nltk_ver = nltk.__version__
        except Exception as e:
            nltk_ok = False
            nltk_ver = f"NOT INSTALLED ({e.__class__.__name__})"
        lines.append(f"nltk        : {nltk_ver}")

        corpus_ok = False
        if nltk_ok:
            try:
                from nltk.corpus import wordnet as wn
                wn.ensure_loaded()
                corpus_ok = len(wn.synsets("test")) > 0
            except Exception:
                corpus_ok = False
        lines.append(f"WordNet data: {'present' if corpus_ok else 'MISSING'}")
        tagger_problem = nltk_resources.pos_tagger_problem()
        tagger_state = ("present" if tagger_problem is None
                        else "UNUSABLE" if tagger_problem.kind == "unusable" else "MISSING")
        lines.append(f"POS tagger  : {tagger_state}")

        # 3. dictionaries
        lines.append("-" * 40)
        for d in REGISTRY.values():
            tag = "*" if d.id == self.config.active_dictionary else " "
            lines.append(f" {tag} {d.id:<10} {'ready' if d.available() else 'unavailable':<12}"
                         f" {'online' if d.requires_network else 'offline'}")
        dic, notice = self.effective_dictionary()
        lines.append("-" * 40)
        lines.append(f"Configured  : {self.config.active_dictionary}")
        lines.append(f"Effective   : {dic.id}  ({'offline' if not dic.requires_network else 'ONLINE — can time out'})")

        # 4. verdict + actionable fix (key off the SYMPTOM: is the effective
        # dictionary offline? If it fell back to online, always warn — then
        # explain the most likely cause.)
        lines.append("=" * 40)
        if not dic.requires_network:
            lines.append("OK: using the offline dictionary. No network needed.")
        elif dic.id == self.config.active_dictionary:
            # Chosen on purpose (DICT USE freedict), not a fallback.
            lines.append(f"OK: using the online dictionary you chose ('{dic.id}'). Lookups need")
            lines.append("the network and can time out.")
            offline = next((d for d in REGISTRY.values()
                            if not d.requires_network and d.available()), None)
            if offline is not None:
                lines.append(f"For offline lookups:  dict use {offline.id}")
            elif packaged:
                lines.append("This copy of the app is missing its built-in WordNet.")
                lines.append(PACKAGED_REPAIR)
            elif not nltk_ok:
                lines.append('For offline lookups:  pip install "vocab-study[wordnet]"'
                             "  then  dict use wordnet")
            else:
                lines.append("For offline lookups:  dict install wordnet  then  dict use wordnet")
        else:
            lines.append("PROBLEM: lookups are using an ONLINE dictionary that can time out,")
            lines.append(f"because the offline default ('{self.config.active_dictionary}') is unavailable here.")
            if packaged and not (nltk_ok and corpus_ok):
                lines.append("Cause: this copy of the app is missing its built-in WordNet. Fix:")
                lines.append(f"  {PACKAGED_REPAIR}")
            elif not nltk_ok:
                lines.append("Cause: nltk is not installed in THIS Python. Fix:")
                lines.append('  pip install "vocab-study[wordnet]"   (into this interpreter)')
                lines.append("  — or launch with the project venv Python / run setup.")
            elif not corpus_ok:
                lines.append("Cause: the WordNet corpus is missing. Fix:")
                lines.append("  dict install wordnet")
            else:
                lines.append(f"Cause: '{self.config.active_dictionary}' reported unavailable. {notice}")
        if tagger_problem and self.config.disambiguator in ("nlp", "ollama"):
            fix = _tagger_fix(tagger_problem)
            lines.append("Note: sense selection cannot filter definitions by the word's part")
            if self.config.disambiguator == "nlp":
                lines.append("of speech, so it ranks them by their overlap with the sentence only.")
            else:  # the local model still chooses among them
                lines.append("of speech before the local model chooses among them.")
            lines.append(f"Cause: {tagger_problem.reason}." + (" Fix:" if fix else ""))
            if fix:
                lines.append(f"  {fix}")
        return "\n".join(lines)

    @property
    def prompt_prefix(self) -> str:
        loc = self.current_list or "~"
        return f"vocab:{loc}> "

    def _resolve_target(self, name: Optional[str]) -> str:
        if name:
            return sanitize_list_name(name)
        if self.current_list:
            return self.current_list
        raise ValueError("No list selected. Use  USE <List>  or pass a list name.")

    # ---- list lifecycle -------------------------------------------------
    @in_transaction
    def cmd_create(self, name: str) -> str:
        self.storage.create(name)
        return f"Created list '{sanitize_list_name(name)}'.  (USE it to make it current)"

    @in_transaction
    def cmd_delete(self, name: str) -> str:
        name = self.storage.canonical_name(name)
        selected = self.current_list
        if selected and self.storage.exists(selected):
            selected = self.storage.canonical_name(selected)
        self.storage.delete(name)
        if selected == name:
            self.current_list = None
        return f"Deleted list '{name}'."

    def cmd_lists(self) -> str:
        names = self.storage.list_names()
        unsupported = self.storage.unsupported_module_dirs()
        if not names and not unsupported:
            return "No lists yet. Create one with:  CREATE <name>"
        out = []
        for n in names:
            marker = "*" if n == self.current_list else " "
            try:
                wl = self.storage.load_words(n)
            except Exception as error:  # one damaged module must not hide the rest
                out.append(f" {marker} {n}  (unreadable: {error})")
                continue
            out.append(f" {marker} {n}  ({len(wl.words)} words)")
        for n in unsupported:
            out.append(f"   {n}  (folder name not supported; rename it in the modules folder)")
        return "Lists:\n" + "\n".join(out)

    def cmd_use(self, name: str) -> str:
        name = sanitize_list_name(name)
        if not self.storage.exists(name):
            raise ListNotFound(f"List '{name}' does not exist. (CREATE it first)")
        self.current_list = name = self.storage.canonical_name(name)
        return f"Now using '{name}'."

    def cmd_where(self) -> str:
        return f"Current list: {self.current_list}" if self.current_list \
            else "No list selected. Use  USE <List>."

    def cmd_ls(self, name: Optional[str]) -> str:
        """Power-user alias: with no arg behaves like LISTS; with a list name,
        shows the words in that list (or the current list)."""
        if name is None and self.current_list is None:
            return self.cmd_lists()
        target = self._resolve_target(name)
        wl = self.storage.load_words(target)
        if not wl.words:
            return f"'{target}' is empty. Add words with:  ADD <word>"
        rows = [f"'{target}' ({len(wl.words)} words):"]
        for key in sorted(wl.words):
            w = wl.words[key]
            more = len(w.kept_senses()) - 1
            rows.append(f"  {w.word:<20} {w.primary_definition()[:70]}"
                        + (f" (+{more} more)" if more else ""))
        return "\n".join(rows)

    # ---- words ----------------------------------------------------------
    def _rank(self, word: str, sentence: str, senses: List[Sense]) -> LookedUp:
        """Put the sense that fits the sentence first (fixes WordNet's
        frequency-first order), saying how the first sense was chosen."""
        if not sentence or len(senses) < 2:
            return LookedUp(senses, "first")
        try:
            from ..study.disambiguate import NlpDisambiguator, get_disambiguator
            dis = get_disambiguator(getattr(self.config, "disambiguator", "nlp"),
                                    getattr(self.config, "ollama_model", "llama3.2:3b"))
            if dis.id == "base":
                return LookedUp(senses, "base")
            ranked = dis.rank(word, sentence, senses)
            # Ollama falls back to, and filters with, the offline ranking.
            offline = dis if isinstance(dis, NlpDisambiguator) else getattr(dis, "fallback", None)
            untagged = isinstance(offline, NlpDisambiguator) and not offline._ensure_tagger()
        except Exception as error:
            return LookedUp(senses, "failed", problem=str(error) or type(error).__name__)
        return LookedUp(ranked, "sentence", untagged=untagged)

    def _lookup_senses(self, word: str, sentence: str = "") -> tuple[List[Sense], str, str]:
        dic, notice = self.effective_dictionary()
        senses = dic.lookup(word)   # may raise LookupFailed
        # Trim only after ranking: dictionaries list senses grouped by part of
        # speech, nouns first, so an earlier cut could drop the one that fits.
        return self._rank(word, sentence, senses).trimmed(SENSE_LIMIT), dic.id, notice

    @staticmethod
    def _sense_note(senses, sentence: str) -> str:
        """The line saying how the first of several saved senses was chosen."""
        if len(senses) < 2:
            return ""
        how = getattr(senses, "how", None)
        if how is None:  # senses from elsewhere: assume they were ranked
            how = "sentence" if sentence.strip() else "first"
        if how == "sentence":
            first = f"Guessed from {quoted_sentence(sentence)}"
            if getattr(senses, "untagged", False):
                first += " without part-of-speech tagging (DOCTOR explains)"
        elif how == "base":
            first = "The dictionary's first sense (DISAMBIG base does not use the sentence)"
        elif how == "failed":
            first = ("The dictionary's first sense (choosing one for the sentence failed: "
                     f"{senses.problem})")
        else:
            first = "The dictionary's first sense"
        more = len(senses) - 1
        return f"    {first}; {more} more sense{'' if more == 1 else 's'} saved."

    def cmd_add(self, word: str, target: Optional[str] = None,
                manual_def: Optional[str] = None, sentence: str = "") -> str:
        named = bool(target)  # ADDL names the module; ADD uses the current one
        target = self._resolve_target(target)
        word_clean = word.strip()
        if not word_clean:
            raise ValueError("No word given.")
        notice = ""
        expected = None
        if manual_def is not None:
            if not manual_def.strip():
                raise ValueError("Definition cannot be empty.")
            senses, dict_id = [Sense(definition=manual_def.strip())], "manual"
        else:
            expected = self.word_state(target, word_clean)
            try:
                senses, dict_id, notice = self._lookup_senses(word_clean, sentence)
            except LookupFailed as e:
                verb = f"ADDL {quote_arg(target)}" if named else "ADD"
                typed = _typed_word(word_clean)
                hint = f"  (add manually:  {verb} {typed} :: <your definition>)" if typed else ""
                raise ValueError(f"{e}{hint if e.recoverable else ''}") from e
        return self.add_resolved_word(word_clean, target, senses, dict_id, notice,
                                      expected=expected, command=True, sentence=sentence)

    @in_transaction
    def word_state(self, target: str, word: str):
        """Snapshot the module's identity and the word's entry before a lookup.

        add_resolved_word compares it at commit time, so a slow lookup cannot
        overwrite a newer edit or land in a module recreated under the same name.
        """
        self.storage.load_words(target)  # reports a missing or damaged module
        return (self.storage.module_identity(target), self.storage.raw_word_entry(target, word))

    def peek_word_state(self, target: str, word: str):
        """word_state() read without the write lock and without writing, for a
        display to see whether a word changed; None when it cannot be read so
        (a missing manifest, a damaged file)."""
        try:
            name = sanitize_list_name(target)
            if not self.paths.manifest_file(name).is_file():
                return None  # module_identity would write one
            return (self.storage.module_identity(name), self.storage.raw_word_entry(name, word))
        except Exception:
            return None

    def add_resolved_word(self, word: str, target: str, senses, dict_id: str,
                          notice: str = "", expected=None, command: bool = False,
                          sentence: str = "") -> str:
        """commit_resolved_word(), returning only its reply."""
        return self.commit_resolved_word(word, target, senses, dict_id, notice, expected,
                                         command, sentence).message

    @in_transaction
    def commit_resolved_word(self, word: str, target: str, senses, dict_id: str,
                             notice: str = "", expected=None, command: bool = False,
                             sentence: str = "") -> "WordAdded":
        """Commit a word on the UI thread, reading the latest module state.

        A dictionary lookup never replaces an existing entry; only an explicit
        ``ADD word :: definition`` (dict_id "manual") does. ``expected`` comes
        from word_state() taken before the lookup. ``command`` says the word was
        typed in a command, so the hint for replacing an entry uses the command
        grammar rather than the Vocab pane's ``word :: definition``.
        ``sentence`` is the one the senses were ranked for; it is kept with
        the word (see saved_context).
        """
        word_clean = word.strip()
        if not word_clean or not senses or not senses[0].definition.strip():
            raise ValueError("A word and a non-empty definition are required.")
        wl = self.storage.load_words(target)
        # Read (and validate) progress before changing anything.
        stats = self.storage.load_stats(target)
        existed = wl.has(word_clean)
        if existed and dict_id != "manual":
            kept = wl.words[wl.normalize_key(word_clean)].word
            typed = _typed_word(word_clean, command)
            how = f" ({typed} :: ...)" if typed else ""
            return WordAdded(f"'{kept}' is already in '{target}'; kept the existing entry. To "
                             f"replace it, add it with your own definition{how}.",
                             False, self.storage.canonical_name(target), kept)
        # Only a commit that writes needs the check (a word deleted meanwhile,
        # or a module recreated under the same name, refuses it).
        if expected is not None and self.word_state(target, word_clean) != expected:
            raise ValueError(f"'{word_clean}' was not added: '{target}' changed while it was "
                             "being looked up, so the current version was kept.")
        if dict_id == "manual":
            # Your own definition replaces the senses, not the sentence you
            # collected the word from.
            context = wl.words[wl.normalize_key(word_clean)].context if existed else ""
        else:
            context = saved_context(word_clean, sentence)
        w = Word(word=word_clean, dictionary=dict_id, senses=list(senses), context=context)
        wl.add(w)
        # give the new word a fresh scheduler card
        stats.setdefault(wl.normalize_key(word_clean), self.scheduler.new_card())
        self.storage.save_words_and_stats(wl, stats)
        verb = "Updated" if existed else "Added"
        msg = (f"{verb} '{word_clean}' -> {target}  [{dict_id}]\n"
               f"    {senses[0].pos + ': ' if senses[0].pos else ''}{senses[0].definition[:100]}")
        note = "" if dict_id == "manual" else self._sense_note(senses, sentence)
        typed = _typed_word(word_clean)
        if note and command and typed:
            # SENSES works on the current module, like ADD and DEL.
            here = self.storage.canonical_name(target) == self.current_module
            note += (f"  Others:  SENSES {typed}" if here else
                     f"  Others:  USE {quote_arg(target)}  then  SENSES {typed}")
        if note:
            msg = f"{msg}\n{note}"
        return WordAdded(f"{msg}\n{notice}" if notice else msg, True,
                         self.storage.canonical_name(target), word_clean)

    @in_transaction
    def cmd_delete_word(self, word: str, target: Optional[str] = None) -> str:
        target = self._resolve_target(target)
        wl = self.storage.load_words(target)
        stats = self.storage.load_stats(target)  # validated before any change
        if not wl.remove(word):
            raise ValueError(f"'{word}' is not in '{target}'.")
        stats.pop(wl.normalize_key(word), None)
        self.storage.save_words_and_stats(wl, stats)
        return f"Removed '{word.strip()}' from '{target}'."

    # ---- a word's senses (SENSES, and the desktop sense picker) ---------
    @in_transaction
    def sense_listing(self, module: str, word: str) -> SenseListing:
        """A word's saved senses, read together with the snapshot that a
        choice made from them is checked against."""
        module = self.storage.canonical_name(module)
        expected = self.word_state(module, word)
        wl = self.storage.load_words(module)
        saved = wl.words.get(wl.normalize_key(word))
        if saved is None:
            raise ValueError(f"'{word.strip()}' is not in '{module}'.")
        return SenseListing(module=module, word=saved.word, dictionary=saved.dictionary,
                            senses=tuple(saved.senses), context=saved.context,
                            expected=expected, chosen=saved.chosen,
                            kept=len(saved.kept_senses()))

    def more_senses(self, listing: SenseListing) -> SenseListing:
        """The listing with its dictionary's senses that are not saved, in the
        dictionary's order, or a note saying why there are none.

        Only the dictionary the word came from is asked, so a word never mixes
        sources. Reads no stored data, so it may run on a worker thread.
        """
        name = listing.dictionary
        if listing.manual:
            return listing.with_extra(note="It has your own definition, so no dictionary "
                                           "senses are listed.")
        if not name:
            return listing.with_extra(note="Its entry does not say which dictionary it came "
                                           "from, so no other senses can be listed.")
        try:
            dic = get_dictionary(name)
        except KeyError:
            return listing.with_extra(note=f"Its dictionary '{name}' is not one this version "
                                           "knows, so no other senses can be listed.")
        if not dic.available():
            return listing.with_extra(note=f"'{name}' is not available here, so its other senses "
                                           "cannot be listed (DOCTOR explains).")
        try:
            senses = dic.lookup(listing.word)
        except Exception as error:  # LookupFailed, or a backend's own failure
            return listing.with_extra(note=f"Could not list the other senses from '{name}': "
                                           f"{error}")
        listing = listing.with_extra(senses)
        if not listing.extra:
            listing = listing.with_extra(note=f"'{name}' has no other senses of "
                                              f"'{listing.word}'.")
        return listing

    @staticmethod
    def _sense_rows(senses, start: int = 1, studied: int = 0) -> List[str]:
        rows = []
        for number, sense in enumerate(senses, start):
            mark = "*" if number - start < studied else " "
            pos = f"{sense.pos}: " if sense.pos else ""
            rows.append(f"{number:>4} {mark} {pos}{sense.definition}")
            if sense.example:
                rows.append(f"         \"{sense.example}\"")
        return rows

    def _describe_senses(self, listing: SenseListing, show_all: bool) -> str:
        typed = _typed_word(listing.word)
        head = f"'{listing.word}' in '{listing.module}'"
        sentence = (f"Sentence: \"{listing.context}\"" if listing.context
                    else "Added without a sentence.")
        if listing.manual:
            lines = [f"{head}: your own definition", sentence]
            lines += self._sense_rows(listing.senses, studied=1)
            if typed:
                lines.append(f"To use dictionary senses instead:  DEL {typed}  then  ADD {typed}"
                             "   (its progress starts over)")
            return "\n".join(lines)
        how = "by you" if listing.chosen else "automatically"
        lines = [f"{head} [{listing.dictionary or 'dictionary not recorded'}]",
                 f"First sense chosen {how}.  {sentence}"]
        lines += self._sense_rows(listing.senses, studied=listing.kept)
        if listing.extra:
            lines.append(f"More from {listing.dictionary} (saved only if you choose one):")
            lines += self._sense_rows(listing.extra, start=len(listing.senses) + 1)
        if listing.note:
            lines.append(f"({listing.note})")
        if typed and len(listing.offered) > 1:
            lines.append(f"* = studied.  Study another:  SENSE 2 {typed}    Several:  "
                         f"SENSE 2,1 {typed}   (its progress is kept)")
        else:
            lines.append("* = studied.")
        if typed and not show_all:
            lines.append(f"SENSES {typed} --all  also lists the dictionary's other senses.")
        return "\n".join(lines)

    @in_transaction
    def choose_senses(self, listing: SenseListing, chosen: List[int],
                      command: bool = False, restart: bool = False) -> tuple[str, SenseListing]:
        """Study ``listing.offered[i]`` for i in ``chosen`` (0-based): move it
        to the front, adding it when it is an extra dictionary sense.

        Refused, writing nothing, when the word or its module changed since
        the listing was read (another window or process, a newer lookup, a
        module recreated under the same name) or the word has your own
        definition. Progress (stats.json) is not touched unless ``restart``
        asks for the word's reviews to start over, as for a new word; then
        both files are written together. Returns the reply (without the new
        numbering) and the word's listing after the change.
        """
        typed = _typed_word(listing.word, command)
        name, module = listing.word, listing.module
        try:
            current = self.word_state(module, name)
        except ListNotFound:
            raise ValueError(f"'{module}' no longer exists, so nothing was changed.") from None
        if current != listing.expected:
            if current[1] is None:
                raise ValueError(f"'{name}' is no longer in '{module}', so nothing was changed.")
            again = f" List them again:  SENSES {typed}" if command and typed else ""
            raise ValueError(f"'{name}' changed since its senses were listed (in another window "
                             f"or program), so nothing was changed.{again}")
        if listing.manual:
            raise ValueError(f"'{name}' has your own definition, so there are no dictionary "
                             "senses to choose.")
        offered = listing.offered
        if not chosen:
            raise ValueError("Choose a sense.")
        if len(set(chosen)) != len(chosen):
            raise ValueError("Give each sense number once.")
        if any(not 0 <= i < len(offered) for i in chosen):
            count = len(offered)
            hint = (f" SENSES {typed} --all lists the dictionary's other senses."
                    if command and typed and not listing.extra else "")
            raise ValueError(f"'{name}' has {count} sense{'' if count == 1 else 's'} to choose "
                             f"from; choose {'1' if count == 1 else f'1 to {count}'}.{hint}")
        wl = self.storage.load_words(module)
        stats = self.storage.load_stats(module) if restart else None  # validated first
        word = wl.words[listing.key]
        new = reorder(word, offered, chosen)
        studied = new.kept_senses()
        what = "; ".join(f"{sense.pos + ': ' if sense.pos else ''}{sense.definition}"
                         for sense in studied)
        what = f" {len(studied)} senses: {what}" if len(studied) > 1 else f": {what}"
        same = new.senses == word.senses and new.kept_senses() == word.kept_senses()
        if same and word.chosen and not restart:
            reply = f"'{name}' in '{module}' already studies{what}  (nothing was changed)"
        else:
            wl.add(new)
            progress = "its progress is kept"
            if restart:
                stats[listing.key] = self.scheduler.new_card()
                self.storage.save_words_and_stats(wl, stats)
                progress = "its reviews start over, due now"
            else:
                self.storage.save_words(wl)  # its progress (stats.json) is kept as it is
            if not same:
                reply = f"'{name}' in '{module}' now studies{what}  ({progress})"
            elif restart:
                reply = f"'{name}' in '{module}' keeps studying{what}  ({progress})"
            else:
                reply = f"'{name}' in '{module}' keeps studying{what}  (now chosen by you)"
            if len(new.senses) > len(word.senses):
                reply += (f"\n    Saved from {listing.dictionary}; '{name}' now has "
                          f"{len(new.senses)} senses.")
        return reply, self.sense_listing(module, name)

    def cmd_sense(self, numbers: str, word: str, restart: bool = False) -> str:
        """SENSE <n>[,<n>...] <word> [--restart]: study sense n (or several,
        in the order given) of the latest SENSES list of word, or of its
        senses as saved when this session listed none; --restart also starts
        its reviews over."""
        chosen = parse_sense_numbers(numbers)
        module = self.storage.canonical_name(self._resolve_target(None))
        listing = self._sense_listings.get((module, word.strip().lower()))
        listed = listing is not None
        if not listed:
            listing = self.sense_listing(module, word)
        reply, after = self.choose_senses(listing, [n - 1 for n in chosen], command=True,
                                          restart=restart)
        self._sense_listings[(module, after.key)] = after
        reply = "\n".join([reply] + self._sense_rows(after.senses, studied=after.kept))
        if not listed:
            reply += "\n(No SENSES list was shown here, so the number followed the saved order.)"
        return reply

    def cmd_senses(self, word: str, show_all: bool = False) -> str:
        """List a word's saved senses (SENSES); with show_all, also the senses
        of its dictionary that were not saved. SENSE numbers refer to the
        latest list shown."""
        listing = self.sense_listing(self._resolve_target(None), word)
        if show_all:
            listing = self.more_senses(listing)
        self._sense_listings[(listing.module, listing.key)] = listing
        return self._describe_senses(listing, show_all)

    # ---- disambiguation (in-context sense selection) -------------------
    def disambig_status(self) -> dict:
        """Return {engine, model, ollama_up} describing sense-selection state."""
        engine = getattr(self.config, "disambiguator", "nlp")
        model = getattr(self.config, "ollama_model", "llama3.2:3b")
        up = False
        if engine == "ollama":
            try:
                from ..study.disambiguate import OllamaDisambiguator
                up = OllamaDisambiguator(model=model)._server_up()
            except Exception:
                up = False
        return {"engine": engine, "model": model, "ollama_up": up}

    def cmd_disambig(self, arg: str = "") -> str:
        """Show or set the sense-selection engine.  arg: '', 'nlp', 'ollama', 'base'."""
        arg = (arg or "").strip().lower()
        valid = {"nlp", "ollama", "base"}
        if not arg:
            st = self.disambig_status()
            lines = [f"Sense selection (how the right definition is chosen from context):",
                     f"  active : {st['engine']}"]
            if st["engine"] == "ollama":
                state = "running" if st["ollama_up"] else "NOT running -> falls back to nlp"
                lines.append(f"  model  : {st['model']}  ({state})")
            problem = (nltk_resources.pos_tagger_problem()
                       if st["engine"] in ("nlp", "ollama") else None)
            if problem:
                fix = _tagger_fix(problem)
                lines.append(f"  note   : {problem.reason}; the POS filter is off"
                             + (f" (fix: {fix})" if fix else ""))
            lines += ["",
                      "  nlp    - POS filter + Lesk, offline, zero cost (default)",
                      "  ollama - local LLM via Ollama; best quality; needs the server running",
                      "  base   - no reordering (raw dictionary order)",
                      "",
                      "Switch with:  DISAMBIG nlp | ollama | base"]
            return "\n".join(lines)
        if arg not in valid:
            return f"Unknown engine '{arg}'. Choose: nlp | ollama | base."
        self.config.change(self.paths, disambiguator=arg)
        if arg == "ollama":
            st = self.disambig_status()
            warn = "" if st["ollama_up"] else \
                f"  (note: Ollama server not detected — will fall back to nlp until it's running)"
            return f"Sense selection set to ollama ({st['model']}).{warn}"
        return f"Sense selection set to {arg}."

    # ---- dictionaries ---------------------------------------------------
    def cmd_dict_list(self) -> str:
        rows = describe_all()
        out = ["Dictionaries (* = active):"]
        for r in rows:
            active = "*" if r["id"] == self.config.active_dictionary else " "
            avail = "ready" if r["available"] else "not installed"
            net = ", online" if r["requires_network"] else ", offline"
            out.append(f" {active} {r['id']:<10} {avail:<14} cost: {r['install_cost']}{net}")
            out.append(f"       {r['description']}")
        out.append("\nActivate:  dict use <id>     Install:  dict install <id>")
        return "\n".join(out)

    def cmd_dict_install(self, dict_id: str) -> str:
        dic = get_dictionary(dict_id)
        if not dic.needs_install():
            return f"'{dict_id}' is already available."
        cost = dic.pending_cost() or "no download"
        # The REPL confirms cost before calling this; here we just do it.
        dic.install()
        if not dic.needs_install():
            return f"Installed '{dict_id}' ({cost})."
        if dic.available():  # WordNet works, but its part-of-speech tagger does not
            problem = nltk_resources.pos_tagger_problem()
            return (f"Installed '{dict_id}', but part-of-speech tagging still does not work"
                    + (f": {problem.reason}." if problem else "."))
        hint = (PACKAGED_REPAIR if _packaged()
                else f"You may need:  pip install \"vocab-study[{dict_id}]\"")
        return f"Attempted install of '{dict_id}' but it is still unavailable. {hint}"

    def cmd_dict_use(self, dict_id: str) -> str:
        dic = get_dictionary(dict_id)
        if not dic.available():
            return (f"'{dict_id}' is not installed yet. "
                    f"Run:  dict install {dict_id}  (cost: {dic.install_cost or 'none'})")
        self.config.change(self.paths, active_dictionary=dict_id)
        return f"Active dictionary is now '{dict_id}'."

    # ---- notes (notes component) ---------------------------------------
    def cmd_note_list(self, module: Optional[str] = None) -> str:
        target = self._resolve_target(module)
        names = self.storage.note_names(target)
        if not names:
            return f"'{target}' has no notes yet.  (NOTE ADD <name>  to create one)"
        return f"Notes in '{target}':\n" + "\n".join(f"  {n}" for n in names)

    def cmd_note_view(self, note: str, module: Optional[str] = None) -> str:
        target = self._resolve_target(module)
        return self.storage.load_note(target, note)

    @in_transaction
    def cmd_note_add(self, note: str, content: Optional[str] = None,
                     module: Optional[str] = None) -> str:
        """Create a note. Without ``content`` an existing note is left exactly as
        it is; with ``content`` (even "") the note is created or its text replaced."""
        target = self._resolve_target(module)
        if content is None:
            try:
                stem = self.storage.create_note(target, note)  # exclusive
            except FileExistsError:
                stem = self.storage.canonical_note_name(target, note)
                return (f"Note '{stem}' already exists in '{target}'; it was left unchanged.  "
                        f"(NOTE VIEW \"{stem}\" shows it; edit it in the desktop app)")
            return f"Created note '{stem}' in '{target}'.  (edit it in the desktop app)"
        existed = self.storage._note_path(target, note).is_file()
        self.storage.save_note(target, note, content)
        stem = self.storage.canonical_note_name(target, note)
        return f"{'Replaced' if existed else 'Created'} note '{stem}' in '{target}'."

    @in_transaction
    def cmd_note_delete(self, note: str, module: Optional[str] = None) -> str:
        target = self._resolve_target(module)
        self.storage.delete_note(target, note)
        return f"Deleted note '{note}' from '{target}'."

    # ---- the folder for files ---------------------------------------------
    _FOLDER_HINT = ("Choose one with  FOLDER <full path>,  or  FOLDER DEFAULT  for your "
                    "Documents folder.")

    def files_folder(self) -> Path:
        """The folder relative paths in file commands start in: the one chosen
        with FOLDER, or the Documents folder. A chosen folder that is missing
        (an unplugged drive) or a damaged setting is an error, never silently
        replaced by another folder, and nothing is created."""
        setting = self.config.files_folder
        if setting == "":
            return _paths.documents_folder()
        where = self.paths.config_file
        if not isinstance(setting, str):
            raise ValueError(f"The folder for files in {where} is not a path ({setting!r}). "
                             f"{self._FOLDER_HINT}")
        folder = expand_home(setting)
        if not folder.is_absolute():
            raise ValueError(f"The folder for files in {where}, {setting}, is not a full path. "
                             f"{self._FOLDER_HINT}")
        if not _paths._is_folder(folder):
            raise ValueError(f"The folder for files, {folder}, is not available. Connect it, or "
                             "choose another with  FOLDER <full path>  (FOLDER DEFAULT uses your "
                             "Documents folder). Paths typed in full still work.")
        return folder

    def user_path(self, text: str) -> Path:
        """The file a command means: a full path as typed, '~' the home folder,
        anything else relative to files_folder(), which only such a path reads."""
        return resolve_user_path(text, self.files_folder)

    def cmd_folder(self, arg: str = "") -> str:
        """FOLDER: show, set (FOLDER <full path>) or reset (FOLDER DEFAULT) the
        folder for files. The setting is saved in config.json."""
        arg = (arg or "").strip()
        if not arg:
            folder = self.files_folder()  # reports a missing or damaged setting
            if self.config.files_folder == "":
                return (f"Relative file paths start in {folder} (your Documents folder). Change "
                        "it with  FOLDER <full path>;  FOLDER DEFAULT goes back to Documents.")
            return (f"Relative file paths start in {folder}.  FOLDER DEFAULT goes back to your "
                    f"Documents folder, {_paths.documents_folder()}.")
        if arg.lower() == "default":
            self.config.change(self.paths, files_folder="")
            return f"Relative file paths now start in your Documents folder, {_paths.documents_folder()}."
        folder = expand_home(arg)
        if not folder.is_absolute():
            raise ValueError(f"FOLDER needs a full path, such as {Path.home() / 'Backups'}.")
        if not _paths._is_folder(folder):
            raise ValueError(f"{folder} is not a folder that exists. Create it first, or "
                             "choose another.")
        self.config.change(self.paths, files_folder=str(folder))
        return f"Relative file paths now start in {folder}."

    # ---- import / export ------------------------------------------------
    def cmd_export(self, name: str, dest: str, include_stats: bool = False) -> str:
        target = self._resolve_target(name)
        p = self.storage.export_list(target, self.user_path(dest), include_stats=include_stats)
        extra = " (with progress)" if include_stats else ""
        return f"Exported '{target}' -> {p}{extra}"

    @in_transaction
    def cmd_import(self, src: str, new_name: Optional[str], with_stats: bool) -> str:
        src = self.user_path(src)
        name = self.storage.import_list(src, new_name=new_name, with_stats=with_stats,
                                        card_problem=self.scheduler.card_problem)
        prog = "imported progress" if with_stats else "fresh progress"
        return f"Imported module '{name}' from {full_path(src)} ({prog}). USE it to start."

    # ---- module backups ---------------------------------------------------
    def backup_destination(self, name: str, dest: Optional[str] = None):
        """Where BACKUP writes, and whether that is a default dated name:
        the folder for files (or a folder given) gets '<Module> <date>.zip';
        a file name without .zip gets it appended."""
        name = self.storage.canonical_name(name)
        if dest is None:
            return _backup.default_backup_path(self.files_folder(), name), True
        path = self.user_path(dest)
        if path.is_dir():
            return _backup.default_backup_path(path, name), True
        if not path.name.lower().endswith(".zip"):
            path = path.with_name(path.name + ".zip")
        return path, False

    def cmd_backup(self, name: str, dest: Optional[str] = None, *, replace: bool = False,
                   progress=None) -> str:
        """Back up a module into one archive. Raises BackupExists when the
        destination exists and ``replace`` was not confirmed."""
        path, auto = self.backup_destination(name, dest)
        try:
            result = _backup.write_backup(self.storage, name, path, scheduler=self.scheduler,
                                          replace=replace, progress=progress, auto_number=auto)
        except (_backup.Cancelled, KeyboardInterrupt):
            return "Backup cancelled; nothing was written."
        return backup_reply(result)

    def cmd_backup_all(self, folder: Optional[str] = None, *, progress=None) -> str:
        """Back up every module into a new dated folder: one archive each."""
        parent = self.user_path(folder) if folder else self.files_folder()
        result = _backup.backup_all(self.storage, parent, scheduler=self.scheduler,
                                    progress=progress)
        problems = [f"! Not backed up: {name}: {reason}" for name, reason in result.failed]
        problems += [f"! Not backed up: {name} (folder name not supported; rename it in the "
                     "modules folder)" for name in result.unsupported]
        if result.total == 0:
            return "No modules to back up."
        lines = []
        if result.cancelled and result.done:
            lines.append(f"Cancelled after backing up {len(result.done)} of {result.total} "
                         f"modules to {result.folder}; the archives there are complete.")
        elif result.cancelled:
            lines.append("Backup cancelled; nothing was written.")
        elif result.done:
            lines.append(f"Backed up {len(result.done)} of {result.total} modules -> "
                         f"{result.folder}")
        else:
            lines.append("No module was backed up; nothing was written.")
        for name, done in result.done:
            lines.append(f"  {done.path.name} ({backup_counts(done.summary)}; "
                         f"{size_text(done.size)})")
            lines += ["  " + line for line in backup_reply(done).splitlines()[1:]]
        return "\n".join(lines + problems)

    def _restore_problem(self, path, error) -> str:
        """The message for a restore that did not happen."""
        try:
            again = f"RESTORE {quote_arg(str(path))} AS <new name>"
        except CommandSyntaxError:
            again = "RESTORE <file> AS <new name>"
        if isinstance(error, _backup.NameProblem):
            return (f"Cannot restore {path}: {error}; nothing was restored. Restore the backup "
                    f"under another name:  {again}")
        if isinstance(error, _backup.NameTaken):
            if error.folder is not None:
                taken = (f"A folder named '{error.name}' already exists in {error.folder} but is "
                         "not a module")
            elif error.meanwhile:
                taken = f"Module '{error.name}' was created while the backup was being restored"
            else:
                taken = f"Module '{error.name}' already exists"
            return f"{taken}; nothing was restored. Restore the backup under another name:  {again}"
        if isinstance(error, FileNotFoundError):
            return f"Backup file not found: {path}. Nothing was restored."
        if isinstance(error, _backup.ContentProblem):
            return (f"Cannot restore {path}: {_backup.relative(error.member)} in the backup "
                    f"cannot be used: {error.reason}. Nothing was restored.")
        if isinstance(error, OSError) and not isinstance(error, _backup.BackupExists):
            reason = error.strerror or str(error)
            where = f" ({error.filename})" if error.strerror and error.filename else ""
            return f"Could not restore {path}: {reason}{where}. Nothing was restored."
        return f"Cannot restore {path}: {error}. Nothing was restored."

    def backup_info(self, src: str):
        """(path, BackupInfo) for a backup file, checking its structure only;
        raises ValueError with the message RESTORE would give."""
        path = self.user_path(src)
        try:
            return path, _backup.read_backup(path)
        except (FileNotFoundError, _backup.ArchiveProblem) as error:
            raise ValueError(self._restore_problem(path, error)) from None

    def cmd_restore(self, src: str, new_name: Optional[str] = None, *, progress=None) -> str:
        """Restore a backup as a new module (never replacing or merging)."""
        path = self.user_path(src)
        try:
            result = _backup.restore_backup(self.storage, path, new_name,
                                            scheduler=self.scheduler, progress=progress)
        except (_backup.Cancelled, KeyboardInterrupt):
            return "Restore cancelled; nothing was restored."
        except Exception as error:  # every failure says that nothing was restored
            raise ValueError(self._restore_problem(path, error)) from error
        return (f"Restored module '{result.name}' from {path} "
                f"({backup_counts(result.summary)}). USE it to start.")

    # ---- stats ----------------------------------------------------------
    def vocabulary_cards(self, name: str):
        """Return words and their current cards, including unstudied imports."""
        wl = self.storage.load_words(name)
        stats = self.storage.load_stats(name)
        return wl, {key: stats.get(key) or self.scheduler.new_card() for key in wl.words}

    def due_count(self, name: str) -> int:
        _wl, cards = self.vocabulary_cards(name)
        return len(self.scheduler.due_order(cards, time.time()))

    def cmd_stats(self, name: Optional[str]) -> str:
        target = self._resolve_target(name)
        wl, stats = self.vocabulary_cards(target)
        now = time.time()
        sched = self.scheduler
        due = sched.due_order(stats, now)
        out = [f"Stats for '{target}':  {len(wl.words)} words, {len(due)} due now",
               f"Algorithm: {sched.name}"]
        for key in sorted(wl.words):
            card = stats.get(key, sched.new_card())
            due_flag = "DUE" if sched.is_due(card, now) else "   "
            out.append(f"  {due_flag} {wl.words[key].word:<18} {sched.summary(card)}")
        return "\n".join(out)

    # ---- study ----------------------------------------------------------
    def run_study(self, names: List[str], prompt: Prompt, echo: Echo,
                  session_label: Optional[str] = None) -> str:
        """Interactive study loop shared by STUDY / STUDYALL (terminal frontend).

        Drives the UI-agnostic StudySession; prompt()/echo() are injected so this
        is testable (tests pass scripted callbacks). The Qt GUI drives the same
        StudySession directly via its event loop instead of this blocking loop.
        """
        mechanical = self.config.mechanical_repetition
        judge = get_judge(self.config.judge_backend) if mechanical else None
        session = StudySession(self.storage, self.scheduler, names,
                               mechanical=mechanical, judge=judge,
                               session_label=session_label)
        if session.total == 0:
            return "Nothing due right now. Come back later, or ADD more words."
        echo(f"Studying: {session.label}   ({session.total} due)   [type 'q' to stop]")

        while not session.done:
            view = session.current()
            echo(f"\n[{view.index}/{view.total}] {view.word}")
            if mechanical:
                ans = ""
                while not ans:  # like the GUI, a blank line is not an answer
                    ans = prompt("  your definition (or 'q'): ").strip()
                if ans.lower() == "q":
                    break
                judgement = session.judge_text(ans)
                if judgement.suggested is None:
                    echo(f"  {judgement.feedback}")
                else:
                    which = f" (sense {judgement.sense})" if judgement.sense else ""
                    echo(f"  score {judgement.score:.0%}{which} — {judgement.feedback}")
                lines = judgement.reference_lines()
                if len(lines) > 1:  # several senses studied: numbered, as revealed
                    echo("  reference:")
                    for line in lines:
                        echo(f"  {line}")
                else:
                    echo(f"  reference: {lines[0]}")
                outcome = self._record_verdict(session, prompt, echo, judgement.suggested)
                if outcome is _STOP:
                    break
                if outcome is not None and outcome.overruled:
                    echo(f"  You overruled the judge: recorded as "
                         f"{'correct' if outcome.correct else 'missed'}.")
            else:
                ans = prompt("  press Enter to reveal (or 'q'): ")
                if ans.strip().lower() == "q":
                    break
                if len(view.senses) > 1:  # several senses studied: show them all
                    for number, sense in enumerate(view.senses, 1):
                        pos = f"{sense.pos}: " if sense.pos else ""
                        echo(f"  {number}. {pos}{sense.definition}")
                        if sense.example:
                            echo(f"     \"{sense.example}\"")
                else:
                    pos = f"{view.pos}: " if view.pos else ""
                    echo(f"  {pos}{view.definition}")
                # Enter means missed, as it always has.
                if self._record_verdict(session, prompt, echo, False) is _STOP:
                    break

        summary = session.finish()
        return f"\n{summary}"

    @classmethod
    def _record_verdict(cls, session: StudySession, prompt: Prompt, echo: Echo,
                        suggested: Optional[bool]):
        """Ask for the verdict on the current card and record it. Returns the
        AnswerOutcome, None when the card was skipped as changed elsewhere, or
        _STOP when the user stopped without recording it. A failed save asks
        again, so the answer is not lost."""
        while True:
            verdict = cls._ask_verdict(prompt, suggested)
            if verdict is None:
                return _STOP
            try:
                return session.answer(verdict)
            except StudyCardUnavailable as error:
                echo(str(error))
                return None
            except (OSError, ValueError) as error:
                echo(f"  Could not save review: {error}")

    @staticmethod
    def _ask_verdict(prompt: Prompt, suggested: Optional[bool]) -> Optional[bool]:
        """Ask whether an answer was right. Enter keeps the suggestion (the
        judge's verdict, or missed in reveal mode); with none (a card the judge
        cannot grade) it asks again. y/yes and n/no choose, q stops (None), and
        any other reply asks again."""
        choices = {None: "[y/n]", True: "[Y/n]", False: "[y/N]"}[suggested]
        while True:
            reply = prompt(f"  did you get it? {choices} (or 'q'): ").strip().lower()
            if reply in ("y", "yes"):
                return True
            if reply in ("n", "no"):
                return False
            if reply == "q":
                return None
            if not reply and suggested is not None:
                return suggested

    def study_targets_all(self) -> List[str]:
        return self.storage.list_names()
