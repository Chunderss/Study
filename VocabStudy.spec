# Build on the target OS: python -m PyInstaller --noconfirm VocabStudy.spec
from pathlib import Path

root = Path(SPECPATH)
corpus = root / "build" / "nltk_data"
if not (corpus / "corpora" / "wordnet.zip").is_file():
    raise SystemExit("Offline dictionary missing. Run python tools/build_desktop.py first.")

a = Analysis(
    [str(root / "packaging" / "desktop.py")],
    pathex=[str(root)],
    datas=[(str(corpus), "nltk_data")],
    hiddenimports=["PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtWebEngineWidgets"],
    excludes=["tkinter", "matplotlib", "scipy", "numpy", "pandas", "torch", "sentence_transformers"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="VocabStudy", console=False, debug=False, strip=False, upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VocabStudy")
