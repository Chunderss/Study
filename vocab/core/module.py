"""The Module manifest — a study container's description on disk (module.json).

A Module groups typed components. Today: vocab (words + Leitner progress),
notes (markdown), documents (reference files). New component types plug in later
without changing this manifest shape — `components` is just a set of enabled
type ids, and the manifest carries a schema version + free-form metadata so the
format can grow (tags, grouping, per-component settings) compatibly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

MODULE_SCHEMA_VERSION = 1

# Component type ids known today. The vocab component is always present (it is
# what a pre-module "list" becomes); notes/documents are optional but enabled by
# default so every module is ready to hold them.
KNOWN_COMPONENTS = ("vocab", "notes", "documents")
DEFAULT_COMPONENTS = ("vocab", "notes", "documents")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Module:
    name: str
    components: List[str] = field(default_factory=lambda: list(DEFAULT_COMPONENTS))
    created: str = field(default_factory=_utcnow_iso)
    metadata: Dict = field(default_factory=dict)   # room to grow: tags, group, etc.

    def has_component(self, component_id: str) -> bool:
        return component_id in self.components

    def to_dict(self) -> dict:
        return {
            "schema": MODULE_SCHEMA_VERSION,
            "name": self.name,
            "components": list(self.components),
            "created": self.created,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict, fallback_name: str = "") -> "Module":
        comps = d.get("components") or list(DEFAULT_COMPONENTS)
        # vocab is always implicitly present
        if "vocab" not in comps:
            comps = ["vocab"] + list(comps)
        return cls(
            name=str(d.get("name", fallback_name)),
            components=list(comps),
            created=str(d.get("created", "")) or _utcnow_iso(),
            metadata=dict(d.get("metadata", {})),
        )
