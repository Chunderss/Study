"""Application state + command handlers.

App is UI-agnostic: every handler returns a string (what to print) and never
calls input()/print() directly, EXCEPT the interactive STUDY loop which needs a
prompt callback. This keeps the whole thing testable — repl.py wires real stdin/
stdout, tests drive App methods directly.
"""
from __future__ import annotations

import time
from typing import Callable, List, Optional

from ..algorithms.base import Review, get_scheduler
from ..core.config import Config
from ..core import migrate as _migrate
from ..core.models import Sense, Word
from ..core.paths import InvalidListName, get_paths, sanitize_list_name, sanitize_module_name
from ..core.storage import ListExists, ListNotFound, ModuleExists, ModuleNotFound, Storage
from ..dictionaries import LookupFailed, describe_all, get_dictionary
from ..study.judge import get_judge
from ..study.session import SessionResult, StudySession, build_session

# Callbacks the REPL supplies so STUDY can be interactive while staying testable.
Prompt = Callable[[str], str]        # prompt(text) -> user input line
Echo = Callable[[str], None]         # echo(text) -> None (print)


class App:
    def __init__(self, root: Optional[str] = None):
        self.paths = get_paths(root)
        # One-time migration from the legacy lists/ layout to modules/.
        self.migration_note = ""
        try:
            if _migrate.needs_migration(self.paths):
                created = _migrate.migrate(self.paths)
                if created:
                    self.migration_note = (
                        f"Migrated {len(created)} list(s) to the new Module format "
                        f"(a backup was saved alongside): {', '.join(created)}")
        except Exception as e:  # never block startup on migration
            self.migration_note = f"(migration skipped: {e})"
        self.storage = Storage(self.paths)
        self.config = Config.load(self.paths)
        # current_module is the active module; current_list kept as an alias.
        self.current_module: Optional[str] = None

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
        import sys
        from ..dictionaries import REGISTRY, get_dictionary

        lines = ["Vocab Study — doctor", "=" * 40]
        # 1. interpreter
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        lines.append(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
        lines.append(f"Virtualenv  : {'yes' if in_venv else 'NO — likely wrong interpreter'}")

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
        else:
            lines.append("PROBLEM: lookups are using an ONLINE dictionary that can time out,")
            lines.append(f"because the offline default ('{self.config.active_dictionary}') is unavailable here.")
            if not nltk_ok:
                lines.append("Cause: nltk is not installed in THIS Python. Fix:")
                lines.append('  pip install "vocab-study[wordnet]"   (into this interpreter)')
                lines.append("  — or launch with the project venv Python / run setup.")
            elif not corpus_ok:
                lines.append("Cause: the WordNet corpus is missing. Fix:")
                lines.append("  dict install wordnet")
            else:
                lines.append(f"Cause: '{self.config.active_dictionary}' reported unavailable. {notice}")
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
    def cmd_create(self, name: str) -> str:
        self.storage.create(name)
        return f"Created list '{sanitize_list_name(name)}'.  (USE it to make it current)"

    def cmd_delete(self, name: str) -> str:
        name = sanitize_list_name(name)
        self.storage.delete(name)
        if self.current_list == name:
            self.current_list = None
        return f"Deleted list '{name}'."

    def cmd_lists(self) -> str:
        names = self.storage.list_names()
        if not names:
            return "No lists yet. Create one with:  CREATE <name>"
        out = []
        for n in names:
            wl = self.storage.load_words(n)
            marker = "*" if n == self.current_list else " "
            out.append(f" {marker} {n}  ({len(wl.words)} words)")
        return "Lists:\n" + "\n".join(out)

    def cmd_use(self, name: str) -> str:
        name = sanitize_list_name(name)
        if not self.storage.exists(name):
            raise ListNotFound(f"List '{name}' does not exist. (CREATE it first)")
        self.current_list = name
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
            rows.append(f"  {w.word:<20} {w.primary_definition()[:70]}")
        return "\n".join(rows)

    # ---- words ----------------------------------------------------------
    def _lookup_senses(self, word: str, sentence: str = "") -> tuple[List[Sense], str, str]:
        dic, notice = self.effective_dictionary()
        senses = dic.lookup(word)   # may raise LookupFailed
        # If we have the sentence the word came from, reorder senses so the one
        # that fits the context is first (fixes WordNet's frequency-first wart).
        if sentence and len(senses) > 1:
            try:
                from ..study.disambiguate import get_disambiguator
                dis = get_disambiguator(getattr(self.config, "disambiguator", "nlp"),
                                        getattr(self.config, "ollama_model", "llama3.2:3b"))
                senses = dis.rank(word, sentence, senses)
            except Exception:
                pass
        return senses, dic.id, notice

    def cmd_add(self, word: str, target: Optional[str] = None,
                manual_def: Optional[str] = None, sentence: str = "") -> str:
        target = self._resolve_target(target)
        wl = self.storage.load_words(target)
        word_clean = word.strip()
        if not word_clean:
            raise ValueError("No word given.")
        notice = ""
        if manual_def is not None:
            senses, dict_id = [Sense(definition=manual_def.strip())], "manual"
        else:
            try:
                senses, dict_id, notice = self._lookup_senses(word_clean, sentence)
            except LookupFailed as e:
                hint = "  (add manually:  ADD {} :: <your definition>)".format(word_clean)
                raise ValueError(f"{e}{hint if e.recoverable else ''}") from e
        w = Word(word=word_clean, dictionary=dict_id, senses=senses)
        existed = wl.has(word_clean)
        wl.add(w)
        self.storage.save_words(wl)
        # give the new word a fresh scheduler card
        stats = self.storage.load_stats(target)
        stats.setdefault(wl.normalize_key(word_clean), self.scheduler.new_card())
        self.storage.save_stats(target, stats)
        verb = "Updated" if existed else "Added"
        msg = (f"{verb} '{word_clean}' -> {target}  [{dict_id}]\n"
               f"    {senses[0].pos + ': ' if senses[0].pos else ''}{senses[0].definition[:100]}")
        return f"{msg}\n{notice}" if notice else msg

    def cmd_delete_word(self, word: str, target: Optional[str] = None) -> str:
        target = self._resolve_target(target)
        wl = self.storage.load_words(target)
        if not wl.remove(word):
            raise ValueError(f"'{word}' is not in '{target}'.")
        self.storage.save_words(wl)
        stats = self.storage.load_stats(target)
        stats.pop(wl.normalize_key(word), None)
        self.storage.save_stats(target, stats)
        return f"Removed '{word.strip()}' from '{target}'."

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
            lines += ["",
                      "  nlp    - POS filter + Lesk, offline, zero cost (default)",
                      "  ollama - local LLM via Ollama; best quality; needs the server running",
                      "  base   - no reordering (raw dictionary order)",
                      "",
                      "Switch with:  DISAMBIG nlp | ollama | base"]
            return "\n".join(lines)
        if arg not in valid:
            return f"Unknown engine '{arg}'. Choose: nlp | ollama | base."
        self.config.disambiguator = arg
        self.config.save(self.paths)
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
        if dic.available():
            return f"'{dict_id}' is already available."
        cost = dic.install_cost or "no download"
        # The REPL confirms cost before calling this; here we just do it.
        dic.install()
        ok = dic.available()
        return (f"Installed '{dict_id}' ({cost})." if ok
                else f"Attempted install of '{dict_id}' but it is still unavailable. "
                     f"You may need:  pip install \"vocab-study[{dict_id}]\"")

    def cmd_dict_use(self, dict_id: str) -> str:
        dic = get_dictionary(dict_id)
        if not dic.available():
            return (f"'{dict_id}' is not installed yet. "
                    f"Run:  dict install {dict_id}  (cost: {dic.install_cost or 'none'})")
        self.config.active_dictionary = dict_id
        self.config.save(self.paths)
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

    def cmd_note_add(self, note: str, content: str = "",
                     module: Optional[str] = None) -> str:
        target = self._resolve_target(module)
        stem = self.storage.save_note(target, note, content)
        where = "the GUI" if not content else "this note"
        return f"Saved note '{stem}' in '{target}'.  (edit it in {where})"

    def cmd_note_delete(self, note: str, module: Optional[str] = None) -> str:
        target = self._resolve_target(module)
        self.storage.delete_note(target, note)
        return f"Deleted note '{note}' from '{target}'."

    # ---- import / export ------------------------------------------------
    def cmd_export(self, name: str, dest: str, include_stats: bool = False) -> str:
        target = self._resolve_target(name)
        p = self.storage.export_list(target, dest, include_stats=include_stats)
        extra = " (with progress)" if include_stats else ""
        return f"Exported '{target}' -> {p}{extra}"

    def cmd_import(self, src: str, new_name: Optional[str], with_stats: bool) -> str:
        name = self.storage.import_list(src, new_name=new_name, with_stats=with_stats)
        prog = "imported progress" if with_stats else "fresh progress"
        return f"Imported module '{name}' ({prog}). USE it to start."

    # ---- stats ----------------------------------------------------------
    def cmd_stats(self, name: Optional[str]) -> str:
        target = self._resolve_target(name)
        wl = self.storage.load_words(target)
        stats = self.storage.load_stats(target)
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
                ans = prompt("  your definition (or 'q'): ").strip()
                if ans.lower() == "q":
                    break
                outcome = session.answer_text(ans)
                echo(f"  score {outcome.score:.0%} — {outcome.feedback}")
                echo(f"  reference: {outcome.reference}")
            else:
                ans = prompt("  press Enter to reveal (or 'q'): ")
                if ans.strip().lower() == "q":
                    break
                pos = f"{view.pos}: " if view.pos else ""
                echo(f"  {pos}{view.definition}")
                verdict = prompt("  did you get it? [y/N]: ").strip().lower()
                session.answer(verdict in ("y", "yes"))

        summary = session.finish()
        return f"\n{summary}"

    def study_targets_all(self) -> List[str]:
        return self.storage.list_names()
