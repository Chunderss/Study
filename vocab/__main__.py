"""Optional terminal entry point; desktop users launch VocabStudy.exe."""
import argparse
import sys

from .cli.app import App
from .cli.repl import Repl
from .core.locking import DataFolderBusy
from .version import __version__


def _startup_problem(error: OSError) -> str:
    """One line for a data folder that cannot be opened or written."""
    if isinstance(error, DataFolderBusy):
        return f"Vocab Study could not start: {error}"
    reason = error.strerror or str(error) or type(error).__name__
    where = f": {error.filename}" if error.filename else ""
    return (f"Vocab Study could not start: {reason}{where}. Choose another data folder "
            "with --home DIR or VOCAB_HOME.")


def _quiet_interrupt(previous):
    """An excepthook that prints nothing for Ctrl-C and defers to ``previous``
    for every other exception."""
    def hook(kind, value, traceback):
        if issubclass(kind, KeyboardInterrupt):
            return
        previous(kind, value, traceback)
    return hook


def main(argv=None):
    parser = argparse.ArgumentParser(description="Vocab Study terminal interface")
    parser.add_argument("--home", help="Data directory")
    parser.add_argument("--version", action="version", version=f"Vocab Study {__version__}")
    args = parser.parse_args(argv)
    try:
        try:
            app = App(args.home)
        except OSError as error:
            # A data folder that cannot be created or written: say which one
            # and how to choose another. Other errors keep their traceback.
            print(_startup_problem(error), file=sys.stderr)
            raise SystemExit(1)
        Repl(app).loop()
    except KeyboardInterrupt:
        # Python itself then ends the process the way an interrupted program
        # ends (killed by SIGINT on POSIX, status 0xC000013A on Windows); the
        # hook only keeps it from printing a traceback.
        sys.stdout.flush()
        sys.excepthook = _quiet_interrupt(sys.excepthook)
        raise


if __name__ == "__main__":
    main()
