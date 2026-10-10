"""Vocab Study's tests. Run them with pytest:  python -m pytest -q

tests/conftest.py gives every test a new temporary VOCAB_HOME and puts this
checkout first on PYTHONPATH; child processes inherit both, so App() without a
root never reaches a real data folder. Most tests also pass their own tmp_path.
NLTK data and network access are not replaced: tests that need real NLTK data
skip without it (see real_nltk.py).
"""
