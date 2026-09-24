"""Dictionary backends.

A Dictionary turns a word into a list of Senses. Many can be installed; exactly
one is active at a time (see Config.active_dictionary). New backends register
themselves in ``registry`` so `dict list/install/use` work uniformly and can
report the disk/download cost before the user commits.
"""
from .base import Dictionary, DictionaryError, LookupFailed
from .registry import REGISTRY, get_dictionary, describe_all

__all__ = [
    "Dictionary", "DictionaryError", "LookupFailed",
    "REGISTRY", "get_dictionary", "describe_all",
]
