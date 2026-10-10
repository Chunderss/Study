r"""Command parsing: one grammar for the terminal and the desktop Console.

A command is a verb followed by arguments::

    CREATE "The Great Gatsby"
    ADDL "The Great Gatsby" ephemeral :: lasting a short time
    IMPORT "C:\Users\Pat Smith\book.json" as Gatsby

The rules are the same on every platform:

- Whitespace separates arguments.
- Backslashes are ordinary characters, so Windows paths such as
  C:\Users\Pat\book.json or \\server\share\book.json need quotes only when
  they contain spaces.
- A double quote at the start of an argument groups it, spaces included, up to
  the double quote that ends the argument (one followed by a space, '::' or the
  end of the line). A double quote that is never closed is an error.
- Single quotes are ordinary characters: don't, Bob's Book, 'tis.
- The first '::' outside double quotes ends the arguments. The text after it
  is the body (a definition or note text), kept as typed apart from the spaces
  around it.

Verbs are case-insensitive; the rest is preserved as typed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

BODY_SEPARATOR = "::"


class CommandSyntaxError(ValueError):
    """The line cannot be split into a command (e.g. an unclosed double quote)."""


@dataclass
class Command:
    verb: str                    # upper-cased
    args: List[str]              # the arguments before '::'
    raw: str
    body: Optional[str] = None   # the text after '::'; None when there is no '::'


def _closing_quote(line: str, start: int) -> int:
    """Index of the double quote closing the argument opened at ``start``, or -1."""
    end = line.find('"', start + 1)
    while end != -1:
        rest = line[end + 1:]
        if not rest or rest[0].isspace() or rest.startswith(BODY_SEPARATOR):
            return end
        end = line.find('"', end + 1)
    return -1


def split_command(line: str) -> Tuple[List[str], Optional[str]]:
    """Split ``line`` into its arguments and its body (None without '::')."""
    args: List[str] = []
    i, n = 0, len(line)
    while i < n:
        if line[i].isspace():
            i += 1
        elif line.startswith(BODY_SEPARATOR, i):
            return args, line[i + len(BODY_SEPARATOR):].strip()
        elif line[i] == '"':
            end = _closing_quote(line, i)
            if end == -1:
                raise CommandSyntaxError(
                    f'Unclosed quote in: {line[i:]}   (end the quoted name or path with ")')
            args.append(line[i + 1:end])
            i = end + 1
        else:
            j = i
            while j < n and not line[j].isspace() and not line.startswith(BODY_SEPARATOR, j):
                j += 1
            args.append(line[i:j])
            i = j
    return args, None


def quote_arg(text: str) -> str:
    """``text`` written as one argument that split_command() reads back unchanged.

    For hints that show a command to type. Double quotes are added only when
    the grammar needs them: the text is empty, contains whitespace or '::', or
    starts with a double quote. Raises CommandSyntaxError when no form exists:
    inside double quotes, a double quote followed by whitespace or '::' would
    end the argument early, and there is no escape character.
    """
    if text and not (text.startswith('"') or BODY_SEPARATOR in text
                     or any(ch.isspace() for ch in text)):
        return text
    quoted = f'"{text}"'
    if _closing_quote(quoted, 0) != len(quoted) - 1:
        raise CommandSyntaxError(f"{text} cannot be written as one argument")
    return quoted


def parse(line: str) -> Optional[Command]:
    """Parse one input line; None for a blank line.

    Raises CommandSyntaxError (a ValueError) when the line cannot be split, so
    callers parse inside the error handling that shows "! <message>".
    """
    line = line.strip()
    if not line:
        return None
    args, body = split_command(line)
    if not args:
        raise CommandSyntaxError("Start with a command, e.g.  ADD <word> :: <definition>")
    return Command(verb=args[0].upper(), args=args[1:], raw=line, body=body)
