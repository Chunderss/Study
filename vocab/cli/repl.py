"""The REPL: read a line, dispatch to an App handler, print the result.

Kept thin on purpose. All real logic lives in App (testable). This file owns
only: the input loop, mapping verbs -> handlers, argument shaping, confirmation
prompts (e.g. before a large dictionary download), and error formatting.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from ..dictionaries import get_dictionary
from .app import App
from .help_text import BANNER, HELP
from .parser import Command, parse


def _takes_body(cmd: Command) -> bool:
    """Only these commands read the text after '::'."""
    return cmd.verb in ("ADD", "ADDL") or (
        cmd.verb == "NOTE" and bool(cmd.args) and cmd.args[0].upper() == "ADD")


def _without_flag(args: List[str], flag: str) -> tuple[List[str], bool]:
    """Remove ``flag`` (any case, any position); report whether it was there."""
    kept = [arg for arg in args if arg.lower() != flag]
    return kept, len(kept) != len(args)


def _path_problem(path: str) -> Optional[str]:
    """A message when a file path starts or ends with a quote character, else None."""
    if path and (path[0] in "\"'" or path[-1] in "\"'"):
        return (f"A file path cannot start or end with a quote: {path}   Only double "
                "quotes group words; quote a path with spaces like this:  "
                "\"C:\\Users\\Pat Smith\\book.json\"")
    return None


class Repl:
    def __init__(self, app: App, in_stream=None, out_stream=None):
        self.app = app
        self.inp = in_stream or sys.stdin
        self.out = out_stream or sys.stdout

    def echo(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def prompt(self, text: str) -> str:
        self.out.write(text)
        self.out.flush()
        line = self.inp.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    def _confirm(self, text: str) -> bool:
        return self.prompt(f"{text} [y/N]: ").strip().lower() in ("y", "yes")

    # ---- dispatch -------------------------------------------------------
    def dispatch(self, cmd: Command) -> Optional[str]:
        v, a = cmd.verb, cmd.args
        app = self.app

        if v in ("QUIT", "EXIT"):
            raise EOFError
        if v == "HELP":
            return HELP
        if cmd.body is not None and not _takes_body(cmd):
            return ("'::' starts a definition or note text, which only ADD, ADDL and NOTE ADD "
                    "take. Quote a word or name that contains '::':  DEL \"std::vector\"")
        if v in ("CREATE", "MODULE"):
            # MODULE with no/ 'new' subverb still creates; MODULES lists.
            if v == "MODULE" and a and a[0].upper() in ("LS", "LIST"):
                return app.cmd_lists()
            return app.cmd_create(" ".join(a)) if a else "Usage: CREATE <name>"
        if v == "DELETE":
            if not a:
                return "Usage: DELETE <module>"
            name = " ".join(a)
            if not self._confirm(f"Delete module '{name}' and all its contents?"):
                return "Cancelled."
            return app.cmd_delete(name)
        if v in ("LISTS", "MODULES"):
            return app.cmd_lists()
        if v in ("USE", "CD"):
            return app.cmd_use(" ".join(a)) if a else "Usage: USE <module>"
        if v in ("WHERE", "PWD"):
            return app.cmd_where()
        if v == "LS":
            return app.cmd_ls(" ".join(a) if a else None)

        if v == "NOTE":
            return self._note(a, cmd.body)

        if v == "ADD":
            if not a:
                return "Usage: ADD <word>   or   ADD <word> :: <definition>"
            return app.cmd_add(" ".join(a), manual_def=cmd.body)
        if v == "ADDL":
            if len(a) < 2:
                return "Usage: ADDL <list> <word>   or   ADDL <list> <word> :: <definition>"
            return app.cmd_add(" ".join(a[1:]), target=a[0], manual_def=cmd.body)
        if v == "DEL":
            return app.cmd_delete_word(" ".join(a)) if a else "Usage: DEL <word>"
        if v == "DELL":
            if len(a) < 2:
                return "Usage: DELL <list> <word>"
            return app.cmd_delete_word(" ".join(a[1:]), target=a[0])

        if v == "DICT":
            return self._dict(a)
        if v == "STATS":
            return app.cmd_stats(" ".join(a) if a else None)
        if v == "ALGO":
            return f"Active algorithm: {app.scheduler.name} ({app.config.active_algorithm})"
        if v == "DOCTOR":
            return app.cmd_doctor()
        if v == "MODE":
            return self._mode(a)
        if v in ("DISAMBIG", "SENSE"):
            return app.cmd_disambig(a[0] if a else "")

        if v == "STUDY":
            names = [" ".join(a)] if a else ([app.current_list] if app.current_list else [])
            if not names or names == [None]:
                return "No list selected. USE <list> or STUDY <list>."
            return app.run_study(names, self.prompt, self.echo)
        if v == "STUDYALL":
            names = app.study_targets_all()
            if not names:
                return "No lists to study. CREATE one first."
            return app.run_study(names, self.prompt, self.echo, session_label="ALL lists")

        if v == "EXPORT":
            rest, stats = _without_flag(a, "--stats")
            if len(rest) != 2:
                return ("Usage: EXPORT <module> <file.json> [--stats]   "
                        "(quote a name or path that contains spaces)")
            return _path_problem(rest[1]) or app.cmd_export(rest[0], rest[1], include_stats=stats)
        if v == "IMPORT":
            return self._import(a)

        return f"Unknown command '{cmd.verb}'. Type HELP."

    def _dict(self, a: List[str]) -> str:
        if not a:
            return self.app.cmd_dict_list()
        sub = a[0].lower()
        if sub == "use" and len(a) > 1:
            return self.app.cmd_dict_use(a[1])
        if sub == "install" and len(a) > 1:
            dic = get_dictionary(a[1])
            if dic.needs_install() and dic.install_cost:
                if not self._confirm(f"'{a[1]}' needs {dic.pending_cost()}. Download now?"):
                    return "Cancelled."
            return self.app.cmd_dict_install(a[1])
        if sub == "list":
            return self.app.cmd_dict_list()
        return "Usage: DICT [list | use <id> | install <id>]"

    def _note(self, a: List[str], body: Optional[str]) -> str:
        """NOTE subcommands:
          NOTE                      list notes in current module
          NOTE LIST [module]        list notes
          NOTE VIEW <name>          print a note's markdown
          NOTE ADD <name>           create an empty note; an existing note is kept
          NOTE ADD <name> :: text   create a note with text, or replace a note's text
          NOTE DEL <name>           delete a note
        """
        if not a:
            return self.app.cmd_note_list()
        sub = a[0].upper()
        if sub in ("LIST", "LS"):
            return self.app.cmd_note_list(" ".join(a[1:]) or None)
        if sub in ("VIEW", "CAT", "SHOW"):
            if len(a) < 2:
                return "Usage: NOTE VIEW <name>"
            return self.app.cmd_note_view(" ".join(a[1:]))
        if sub == "ADD":
            # body is None without '::', so an existing note is never blanked.
            name = " ".join(a[1:])
            if not name:
                return "Usage: NOTE ADD <name> [:: markdown text]"
            return self.app.cmd_note_add(name, body)
        if sub in ("DEL", "DELETE", "RM"):
            if len(a) < 2:
                return "Usage: NOTE DEL <name>"
            return self.app.cmd_note_delete(" ".join(a[1:]))
        return "Usage: NOTE [list | view <name> | add <name> [:: text] | del <name>]"

    def _mode(self, a: List[str]) -> str:
        cfg = self.app.config
        if not a:
            state = "ON" if cfg.mechanical_repetition else "OFF"
            return f"Mechanical repetition (write-the-definition): {state}  (judge: {cfg.judge_backend})"
        if a[0].lower() == "mech" and len(a) > 1:
            cfg.change(self.app.paths, mechanical_repetition=a[1].lower() in ("on", "true", "1", "yes"))
            return f"Mechanical repetition {'ON' if cfg.mechanical_repetition else 'OFF'}."
        return "Usage: MODE [MECH ON|OFF]"

    def _import(self, a: List[str]) -> str:
        usage = ("Usage: IMPORT <file.json> [as <name>] [--stats]   "
                 "(quote a path that contains spaces)")
        rest, stats = _without_flag(a, "--stats")
        if not rest:
            return usage
        src, new_name = rest[0], None
        if len(rest) > 1:
            # Anything after the path must be AS <name>; the name may be several words.
            if rest[1].lower() != "as" or len(rest) < 3:
                return usage
            new_name = " ".join(rest[2:])
        return _path_problem(src) or self.app.cmd_import(src, new_name, with_stats=stats)

    # ---- loop -----------------------------------------------------------
    def _startup_checks(self) -> None:
        """Make the offline dictionary 'just work' on first run.

        If the configured dictionary is WordNet and nltk is installed but the
        corpus isn't downloaded yet, fetch it and the part-of-speech tagger
        automatically (one time, see WordNet.install_cost) so the user never has
        to think about it. A corpus without the tagger is left alone: DOCTOR
        reports it and DICT INSTALL wordnet adds it. If WordNet can't be made
        available at all (nltk missing), warn loudly and point at DOCTOR instead
        of silently falling back to the online API (which times out).
        """
        from ..dictionaries import get_dictionary
        try:
            preferred = get_dictionary(self.app.config.active_dictionary)
        except Exception:
            return
        if preferred.available():
            return
        # Is this the corpus-missing case (nltk importable) or nltk-missing?
        nltk_present = False
        try:
            import nltk  # noqa: F401
            nltk_present = True
        except Exception:
            nltk_present = False

        if preferred.id == "wordnet" and nltk_present:
            self.echo("First run: downloading the offline WordNet dictionary, one time: "
                      f"{preferred.install_cost}...")
            try:
                preferred.install()
            except Exception as e:
                self.echo(f"  could not download automatically: {e}")
            if preferred.available():
                self.echo("  done — the dictionary now works fully offline.\n")
                return
        # Still unavailable: warn instead of silently going online.
        dic, notice = self.app.effective_dictionary()
        if dic.requires_network:
            self.echo("WARNING: the offline dictionary isn't available, so lookups will use")
            self.echo("an online service that may time out. Run  DOCTOR  for the exact fix.\n")

    def loop(self) -> None:
        self.echo(BANNER)
        if getattr(self.app, "migration_note", ""):
            self.echo(self.app.migration_note + "\n")
        if self.app.config.load_error:
            self.echo("! " + self.app.config.load_error + "\n")
        self._startup_checks()
        while True:
            try:
                line = self.prompt(self.app.prompt_prefix)
            except EOFError:
                self.echo("\nGoodbye.")
                return
            try:
                cmd = parse(line)  # a syntax error is reported like any other
                if cmd is None:
                    continue
                result = self.dispatch(cmd)
            except EOFError:
                self.echo("Goodbye.")
                return
            except Exception as e:  # handler-level errors shown, loop survives
                self.echo(f"! {e}")
                continue
            if result:
                self.echo(result)


def run(root: Optional[str] = None) -> None:
    Repl(App(root)).loop()
