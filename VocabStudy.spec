# Build on the target OS: python -m PyInstaller --noconfirm VocabStudy.spec
from pathlib import Path
import sys

root = Path(SPECPATH)
sys.path.insert(0, str(root))
from vocab.nltk_resources import NLTK_PACKAGES

corpus = root / "build" / "nltk_data"
missing = [package for package, bundled in NLTK_PACKAGES.items() if not (corpus / bundled).is_file()]
if missing:
    raise SystemExit(f"Offline NLTK data missing ({', '.join(missing)}). "
                     "Run python tools/build_desktop.py first.")

a = Analysis(
    [str(root / "packaging" / "desktop.py")],
    pathex=[str(root)],
    hiddenimports=["PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtWebEngineWidgets"],
    excludes=["tkinter", "matplotlib", "scipy", "numpy", "pandas", "torch", "sentence_transformers"],
)
# PyInstaller's nltk hook adds every NLTK data folder on this machine
# (~/nltk_data, %APPDATA%\nltk_data, NLTK_DATA), including a tagger that
# nltk.download unzipped, which NLTK would read before the verified zip.
# Ship only the packages the app uses, as the zips NLTK reads in place.
a.datas = [entry for entry in a.datas if Path(entry[0]).parts[:1] != ("nltk_data",)] + [
    (str(Path("nltk_data") / bundled), str(corpus / bundled), "DATA")
    for bundled in NLTK_PACKAGES.values()]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="VocabStudy", console=False, debug=False, strip=False, upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VocabStudy")
