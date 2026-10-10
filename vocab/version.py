"""The application version: the one place to change it.

pyproject.toml reads it (dynamic version), and the banner, the dictionary
User-Agent and ``vocab --version`` import it, so a source checkout, an
installed package and the packaged app all show the same number without
needing installed package metadata.
"""
__version__ = "0.2.0"
