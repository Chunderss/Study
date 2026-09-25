"""Source-linked questions and concepts, with explicit self-rated reviews."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import time
import uuid

from .storage import _atomic_write, _read_json, ModuleNotFound
from ..algorithms.base import Review
from ..algorithms.leitner import LeitnerScheduler


def source_label(source):
    label = source.get("filename") or source.get("label") or "Manual capture"
    if source.get("kind") == "pdf":
        return f"{label} · page {source.get('page', 0) + 1}"
    if source.get("kind") == "epub":
        return f"{label} · chapter {source.get('chapter', 0) + 1}"
    return label


class LearningStore:
    def __init__(self, storage):
        self.storage = storage
        self.scheduler = LeitnerScheduler()

    def _path(self, module):
        if not self.storage.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        return self.storage.paths.module_dir(module) / "learning.json"

    def load(self, module):
        data = _read_json(self._path(module), {"schema": 1, "items": {}})
        if (not isinstance(data, dict) or data.get("schema") != 1 or
                not isinstance(data.get("items"), dict)):
            raise ValueError("Cannot read learning.json; the original file was left untouched.")
        for key, item in data["items"].items():
            if (not isinstance(item, dict) or item.get("id") != key or
                    item.get("kind") not in ("concept", "question") or
                    not all(isinstance(item.get(k), str) for k in ("prompt", "quote", "explanation")) or
                    not isinstance(item.get("source"), dict)):
                raise ValueError("Invalid learning entry; learning.json was left untouched.")
        return data["items"]

    def save_capture(self, module, *, kind, prompt, quote, explanation="", source=None,
                     resolved=False, item_id=None, revision=None):
        if kind not in ("concept", "question"):
            raise ValueError("Choose Concept or Open question.")
        if not prompt.strip() or not quote.strip():
            raise ValueError("Add a question and a source passage first.")
        items = self.load(module)
        if item_id:
            item = self._current(items, item_id, revision)
        else:
            item_id = uuid.uuid4().hex
            item = {"id": item_id, "created": time.time(), "revision": 0,
                    "card": self.scheduler.new_card(), "attempts": []}
        item.update(kind=kind, prompt=prompt.strip(), quote=quote.strip(),
                    explanation=explanation.strip(), source=deepcopy(source or {}),
                    resolved=bool(resolved), revision=item["revision"] + 1)
        items[item_id] = item
        _atomic_write(self._path(module), {"schema": 1, "items": items})
        return item

    @staticmethod
    def _current(items, item_id, revision):
        if item_id not in items:
            raise ValueError("This capture was deleted. Your text has been kept in this window.")
        item = items[item_id]
        if revision != item["revision"]:
            raise ValueError("This capture changed in another window. Reopen it before saving.")
        return item

    def review(self, module, item_id, revision, answer, outcome, now=None):
        if not answer.strip():
            raise ValueError("Try an explanation first (or write what you cannot recall).")
        outcome = Review(outcome)
        items = self.load(module)
        item = self._current(items, item_id, revision)
        if item["kind"] != "concept":
            raise ValueError("Only concepts have scheduled reviews.")
        now = time.time() if now is None else now
        item["card"] = self.scheduler.review(item["card"], outcome, now)
        item["attempts"].append({"time": now, "answer": answer.strip(), "rating": outcome.value})
        item["revision"] += 1
        _atomic_write(self._path(module), {"schema": 1, "items": items})
        return item

    def delete(self, module, item_id, revision):
        items = self.load(module)
        self._current(items, item_id, revision)
        del items[item_id]
        _atomic_write(self._path(module), {"schema": 1, "items": items})

    def due(self, items, now=None):
        now = time.time() if now is None else now
        return self.scheduler.due_order({key: item["card"] for key, item in items.items()
                                         if item["kind"] == "concept"}, now)

    def status(self, item):
        if item["kind"] == "question":
            return "Resolved" if item["resolved"] else "Open question"
        due = item["card"]["due"]
        return "Due now" if due <= time.time() else "Review " + datetime.fromtimestamp(due).strftime("%b %d")
