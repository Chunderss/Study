"""Build a portable desktop folder and ZIP on the current operating system.

    python tools/build_desktop.py              # NLTK data, then the app and ZIP
    python tools/build_desktop.py --data-only  # only download and check the NLTK data
"""
from pathlib import Path
import os
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vocab.nltk_resources import NLTK_PACKAGES  # noqa: E402

# Runs in a fresh interpreter that sees only a copy of the zips the app ships,
# so data installed elsewhere on this machine (or NLTK's unzipped copy of the
# tagger beside its zip) cannot make a broken bundle pass. NLTK also caches a
# loaded tagger in-process.
_VERIFY = r"""
import sys
import nltk
data = sys.argv[1]
nltk.data.path[:] = [data]
def refuse(*args, **kwargs):
    raise AssertionError("the check tried to download NLTK data")
nltk.download = refuse
from nltk.corpus import wordnet
wordnet.ensure_loaded()
assert wordnet.synsets("test"), "WordNet has no senses for 'test'"
from vocab.nltk_resources import POS_TAGGER, pos_tagger_problem
problem = pos_tagger_problem()
assert problem is None, problem.reason
found = str(nltk.data.find(f"taggers/{POS_TAGGER}/"))
assert found.startswith(data), f"tagger found outside the bundle: {found}"
from vocab.study.disambiguate import NlpDisambiguator
pos = NlpDisambiguator()._wn_pos_of("flag", "His energy began to flag.")
assert pos == "v", f"part of speech of 'flag' was {pos!r}"
"""


def verify_nltk_data(corpus: Path) -> None:
    """Prove the zips the spec bundles work offline, on their own."""
    missing = [bundled for bundled in NLTK_PACKAGES.values() if not (corpus / bundled).is_file()]
    if missing:
        raise SystemExit(f"Offline NLTK data verification failed: missing {', '.join(missing)}")
    # Stage beside the data, not in the system temp folder NLTK distrusts.
    with tempfile.TemporaryDirectory(prefix="nltk_check_", dir=corpus.parent) as staging:
        for bundled in NLTK_PACKAGES.values():
            target = Path(staging) / bundled
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(corpus / bundled, target)
        result = subprocess.run([sys.executable, "-c", _VERIFY, str(Path(staging))], cwd=ROOT,
                                env={**os.environ, "PYTHONPATH": str(ROOT)},
                                capture_output=True, text=True)
    if result.returncode:
        raise SystemExit("Offline NLTK data verification failed:\n" + result.stderr[-2000:])


def provision_nltk_data(corpus: Path, download=None) -> None:
    """Download the NLTK packages the app bundles, then verify them."""
    if download is None:
        import nltk
        download = nltk.download
    # NLTK only downloads into a private folder (not group/world writable).
    corpus.mkdir(mode=0o700, parents=True, exist_ok=True)
    corpus.chmod(0o700)
    for package, bundled in NLTK_PACKAGES.items():
        if not (corpus / bundled).is_file() and not download(
                package, download_dir=str(corpus), quiet=True, raise_on_error=True):
            raise SystemExit(f"Could not download the NLTK data package '{package}'.")
    verify_nltk_data(corpus)


def main():
    corpus = ROOT / "build" / "nltk_data"
    provision_nltk_data(corpus)
    if "--data-only" in sys.argv[1:]:
        print(f"NLTK data ready in {corpus}")
        return
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    str(ROOT / "VocabStudy.spec")], cwd=ROOT, check=True)
    shutil.copy2(ROOT / "README.md", ROOT / "dist" / "VocabStudy" / "README.md")
    artifact = shutil.make_archive(str(ROOT / "dist" / f"VocabStudy-{platform.system()}"),
                                   "zip", ROOT / "dist", "VocabStudy")
    print(f"Built {artifact}")


if __name__ == "__main__":
    main()
