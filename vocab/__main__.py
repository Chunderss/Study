"""Optional terminal entry point; desktop users launch VocabStudy.exe."""
import argparse
from .cli.repl import run


def main():
    parser = argparse.ArgumentParser(description="Vocab Study terminal interface")
    parser.add_argument("--home", help="Data directory")
    args = parser.parse_args()
    run(args.home)


if __name__ == "__main__":
    main()
