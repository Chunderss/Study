"""Build a portable desktop folder and ZIP on the current operating system."""
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    import nltk
    corpus = ROOT / "build" / "nltk_data"
    corpus.mkdir(mode=0o700, parents=True, exist_ok=True)
    corpus.chmod(0o700)
    # English lookups need WordNet only, not the much larger multilingual corpus.
    if not (corpus / "corpora" / "wordnet.zip").is_file() and not nltk.download(
            "wordnet", download_dir=str(corpus), quiet=True, raise_on_error=True):
        raise SystemExit("Could not download the offline dictionary.")
    nltk.data.path.insert(0, str(corpus))
    from nltk.corpus import wordnet
    wordnet.ensure_loaded()
    if not wordnet.synsets("test"):
        raise SystemExit("Offline dictionary verification failed.")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    str(ROOT / "VocabStudy.spec")], cwd=ROOT, check=True)
    shutil.copy2(ROOT / "README.md", ROOT / "dist" / "VocabStudy" / "README.md")
    artifact = shutil.make_archive(str(ROOT / "dist" / f"VocabStudy-{platform.system()}"),
                                   "zip", ROOT / "dist", "VocabStudy")
    print(f"Built {artifact}")


if __name__ == "__main__":
    main()
