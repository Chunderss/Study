"""Dictionary registry — the single place backends are discovered.

Add a new backend by importing it and adding one line to ``_BACKENDS``. Nothing
else in the program needs to change (`dict list/install/use` and word lookups
all route through here).
"""
from __future__ import annotations

from typing import Dict, List

from .base import Dictionary
from .freedict import FreeDictionary
from .wordnet import WordNet

# Order here is display order in `dict list`.
_BACKENDS: List[Dictionary] = [
    FreeDictionary(),
    WordNet(),
]

REGISTRY: Dict[str, Dictionary] = {b.id: b for b in _BACKENDS}


def get_dictionary(dict_id: str) -> Dictionary:
    try:
        return REGISTRY[dict_id]
    except KeyError:
        raise KeyError(
            f"Unknown dictionary '{dict_id}'. Known: {', '.join(REGISTRY)}"
        )


def describe_all() -> List[dict]:
    """Rows for `dict list`, including availability + install cost."""
    rows = []
    for b in _BACKENDS:
        rows.append({
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "install_cost": b.install_cost or "none",
            "requires_network": b.requires_network,
            "available": b.available(),
        })
    return rows
