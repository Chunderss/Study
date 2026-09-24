"""Qt desktop frontend for Vocab Study.

This package is a *second frontend* over the same UI-agnostic `App` and
`StudySession` used by the terminal REPL — no domain logic lives here. It just
renders state and forwards user actions to `App.cmd_*` / `StudySession`.
"""
