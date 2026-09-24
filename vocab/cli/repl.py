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


def _extract_manual_def(raw: str) -> tuple[list, str | None]:
    """Split a line on '::' into (tokens_before_::, manual_definition|None).

    Shared by ADD and ADDL so both support:  <verb> ... <word> :: <definition>
    The returned tokens still include the verb at index 0.
    """
    import shlex
    if "::" in raw:
        left, right = raw.split("::", 1)
        try:
            tokens = shlex.split(left, posix=True)
        except ValueError:
            tokens = left.split()
        return tokens, right.strip()
    try:
        tokens = shlex.split(raw, posix=True)
    except ValueError:
        tokens = raw.split()
    return tokens, None


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
        v, a, raw = cmd.verb, cmd.args, cmd.raw
        app = self.app

        if v in ("QUIT", "EXIT"):
            raise EOFError
        if v == "HELP":
            return HELP
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
            return self._note(a, raw)

        if v == "ADD":
            tokens, manual = _extract_manual_def(raw)
            word = tokens[1] if len(tokens) > 1 else ""
            if not word:
                return "Usage: ADD <word>   or   ADD <word> :: <definition>"
            return app.cmd_add(word, manual_def=manual)
        if v == "ADDL":
            tokens, manual = _extract_manual_def(raw)
            # tokens: [ADDL, <list>, <word>...]
            if len(tokens) < 3:
                return "Usage: ADDL <list> <word>   or   ADDL <list> <word> :: <definition>"
            return app.cmd_add(tokens[2], target=tokens[1], manual_def=manual)
        if v == "DEL":
            return app.cmd_delete_word(" ".join(a)) if a else "Usage: DEL <word>"
        if v == "DELL":
            if len(a) < 2:
                return "Usage: DELL <list> <word>"
            return app.cmd_delete_word(a[1], target=a[0])

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
            if len(a) < 2:
                return "Usage: EXPORT <list> <file.json> [--stats]"
            return app.cmd_export(a[0], a[1], include_stats="--stats" in a)
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
            if not dic.available() and dic.install_cost:
                if not self._confirm(f"'{a[1]}' needs {dic.install_cost}. Download now?"):
                    return "Cancelled."
            return self.app.cmd_dict_install(a[1])
        if sub == "list":
            return self.app.cmd_dict_list()
        return "Usage: DICT [list | use <id> | install <id>]"

    def _note(self, a: List[str], raw: str) -> str:
        """NOTE subcommands:
          NOTE                      list notes in current module
          NOTE LIST [module]        list notes
          NOTE VIEW <name>          print a note's markdown
          NOTE ADD <name> [:: text] create/overwrite a note (edit body in the GUI)
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
            # support optional inline body:  NOTE ADD <name> :: <markdown>
            body = ""
            rest = raw.split(None, 2)  # ["NOTE", "ADD", "<name...>"]
            name_and_body = rest[2] if len(rest) > 2 else ""
            if "::" in name_and_body:
                name, body = [s.strip() for s in name_and_body.split("::", 1)]
            else:
                name = name_and_body.strip()
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
            cfg.mechanical_repetition = a[1].lower() in ("on", "true", "1", "yes")
            cfg.save(self.app.paths)
            return f"Mechanical repetition {'ON' if cfg.mechanical_repetition else 'OFF'}."
        return "Usage: MODE [MECH ON|OFF]"

    def _import(self, a: List[str]) -> str:
        if not a:
            return "Usage: IMPORT <file.json> [as <name>] [--stats]"
        src = a[0]
        new_name = None
        if "as" in a:
            i = a.index("as")
            if i + 1 < len(a):
                new_name = a[i + 1]
        return self.app.cmd_import(src, new_name, with_stats="--stats" in a)

    # ---- loop -----------------------------------------------------------
    def _startup_checks(self) -> None:
        """Make the offline dictionary 'just work' on first run.

        If the configured dictionary is WordNet and nltk is installed but the
        corpus isn't downloaded yet, fetch it automatically (one-time, ~40 MB)
        so the user never has to think about it. If WordNet can't be made
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
            self.echo("First run: downloading the offline WordNet dictionary (~40 MB, one time)...")
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
        self._startup_checks()
        while True:
            try:
                line = self.prompt(self.app.prompt_prefix)
            except EOFError:
                self.echo("\nGoodbye.")
                return
            cmd = parse(line)
            if cmd is None:
                continue
            try:
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
