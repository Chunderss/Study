"""Source-linked questions and concepts, with explicit self-rated reviews."""
from __future__ import annotations

from copy import deepcopy
import math
from datetime import datetime
import time
import uuid

from .storage import DataFileError, _atomic_write, _read_json, ModuleNotFound, in_transaction
from ..algorithms.base import Review
from ..algorithms.leitner import LeitnerScheduler


def source_label(source):
    label = source.get("filename") or source.get("label") or "Manual capture"
    if source.get("kind") == "pdf":
        return f"{label} · page {source.get('page', 0) + 1}"
    if source.get("kind") == "epub":
        return f"{label} · chapter {source.get('chapter', 0) + 1}"
    return label


def _is_time(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _capture_problem(key, item, scheduler):
    """Why a stored capture cannot be used, or None."""
    if not isinstance(item, dict):
        return "it is not an object"
    if item.get("id") != key:
        return "its id does not match its key"
    if item.get("kind") not in ("concept", "question"):
        return "'kind' must be concept or question"
    for field in ("prompt", "quote", "explanation"):
        if not isinstance(item.get(field), str):
            return f"'{field}' must be text"
    if not isinstance(item.get("source"), dict):
        return "'source' must be an object"
    if not isinstance(item.get("resolved"), bool):
        return "'resolved' must be true or false"
    if not _is_time(item.get("created")):
        return "'created' must be a time"
    revision = item.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return "'revision' must be a whole number"
    attempts = item.get("attempts")
    if not isinstance(attempts, list):
        return "'attempts' must be a list"
    for attempt in attempts:
        if (not isinstance(attempt, dict) or not _is_time(attempt.get("time")) or
                not isinstance(attempt.get("answer"), str) or not isinstance(attempt.get("rating"), str)):
            return "an attempt is incomplete"
    card = item.get("card")
    problem = scheduler.card_problem(card)
    if problem:
        return problem
    if "due" not in card:  # the Learning list shows it
        return "progress has no due time"
    return None


class LearningStore:
    def __init__(self, storage):
        self.storage = storage
        self.scheduler = LeitnerScheduler()

    def _path(self, module):
        if not self.storage.exists(module):
            raise ModuleNotFound(f"Module '{module}' does not exist.")
        return self.storage.paths.module_dir(module) / "learning.json"

    def load(self, module):
        """Return the module's captures, checked against everything the app
        reads from them; a damaged file raises DataFileError and is left as is."""
        path = self._path(module)
        data = _read_json(path, {"schema": 1, "items": {}})
        if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
            raise DataFileError(path, 'expected {"schema": 1, "items": {...}}')
        if data.get("schema") != 1:
            raise DataFileError(path, f"unsupported version {data.get('schema')!r}")
        for key, item in data["items"].items():
            problem = _capture_problem(key, item, self.scheduler)
            if problem:
                raise DataFileError(path, f"capture {key!r}: {problem}")
        return data["items"]

    @in_transaction
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

    @in_transaction
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

    @in_transaction
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
