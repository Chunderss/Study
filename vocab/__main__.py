"""Optional terminal entry point; desktop users launch VocabStudy.exe."""
import argparse
from .cli.repl import run
from .version import __version__


def main():
    parser = argparse.ArgumentParser(description="Vocab Study terminal interface")
    parser.add_argument("--home", help="Data directory")
    parser.add_argument("--version", action="version", version=f"Vocab Study {__version__}")
    args = parser.parse_args()
    run(args.home)


if __name__ == "__main__":
    main()
