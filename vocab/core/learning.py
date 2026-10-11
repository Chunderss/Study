"""Source-linked questions and concepts, with explicit self-rated reviews."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import cached_property
import math
from datetime import datetime
import time
import unicodedata
import uuid

from .storage import DataFileError, _atomic_write, _read_json, ModuleNotFound, in_transaction
from .text import drop_latin_accents, typed_punctuation
from ..algorithms.base import Review
from ..algorithms.leitner import LeitnerScheduler


def source_label(source):
    label = next((source[key] for key in ("filename", "label")
                  if isinstance(source.get(key), str) and source[key]), "Manual capture")
    kind = source.get("kind")
    if isinstance(kind, str) and kind in _PLACE_FIELD:
        field = _PLACE_FIELD[kind]
        number = source.get(field, 0)
        if _whole(number):
            return f"{label} · {field} {number + 1}"
    return label


# ---- search and filters for the Learning list ---------------------------

def search_text(text):
    """text as the Learning search compares it: NFKC, case-folded, with
    accents on Latin letters dropped as grading drops them ('Café' -> 'cafe',
    'col·legi' -> 'collegi'), other scripts keeping their marks, and typeset
    apostrophes, quotes and hyphens written as typed ('don’t' -> "don't").
    The query and the capture text are both folded, so either may have them."""
    if text.isascii():
        return text.lower()
    text = typed_punctuation(unicodedata.normalize("NFKC", text).casefold())
    return text if text.isascii() else drop_latin_accents(text)


#: The capture fields a search looks in: the question, the passage and the notes.
SEARCHED_FIELDS = ("prompt", "quote", "explanation")


def search_fields(item):
    """The capture text a search looks in, SEARCHED_FIELDS folded by
    search_text. Earlier review answers are not searched."""
    return tuple(search_text(item[field]) for field in SEARCHED_FIELDS)


def _whole(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


_PLACE_FIELD = {"pdf": "page", "epub": "chapter"}


def source_keys(source):
    """(document, place) of a capture's source for the Source filter.

    document is ("file", filename), ("label", label) for an older source with
    only a label, or ("manual",). place is ("pdf", page) or ("epub", chapter)
    within a file, or None, also when the stored number is unusable.
    """
    filename, label = source.get("filename"), source.get("label")
    if not (isinstance(filename, str) and filename):
        if isinstance(label, str) and label:
            return ("label", label), None
        return ("manual",), None
    kind = source.get("kind")
    number = (source.get(_PLACE_FIELD[kind])
              if isinstance(kind, str) and kind in _PLACE_FIELD else None)
    return ("file", filename), ((kind, number) if _whole(number) else None)


def source_choice_label(key):
    """The Source filter's name for a document or one of its places."""
    if key[0] == "manual":
        return "Manual captures"
    if len(key) == 4:
        return source_label({"kind": key[2], "filename": key[1], _PLACE_FIELD[key[2]]: key[3]})
    return key[1]


def source_choices(items, keep=None):
    """Source filter keys: documents sorted by name, each followed by its
    pages or chapters in order, then manual captures. keep, the chosen key,
    stays listed in its place after its last capture is gone."""
    places = {}
    for item in items.values():
        document, place = source_keys(item["source"])
        places.setdefault(document, set())
        if place is not None:
            places[document].add(place)
    if keep is not None:
        document = keep[:1] if keep[0] == "manual" else keep[:2]
        places.setdefault(document, set())
        if len(keep) == 4:
            places[document].add(keep[2:])
    keys = []
    for document in sorted((d for d in places if d[0] != "manual"),
                           key=lambda d: (d[1].casefold(), d)):
        keys.append(document)
        keys.extend(document + place for place in sorted(places[document]))
    if ("manual",) in places:
        keys.append(("manual",))
    return keys


# Status filter keys and their menu text, in menu order.
STATUS_FILTERS = {
    "all": "All captures",
    "open": "Open questions",
    "resolved": "Resolved questions",
    "questions": "All questions",
    "due": "Concepts due now",
    "later": "Concepts not due yet",
    "concepts": "All concepts",
}


def _saved_source_key(value):
    """A Source filter key read back from saved state, or None."""
    if not isinstance(value, (list, tuple)):
        return None
    key = tuple(value)
    if key == ("manual",):
        return key
    if len(key) >= 2 and isinstance(key[1], str) and key[1]:
        if len(key) == 2 and key[0] in ("file", "label"):
            return key
        if (len(key) == 4 and key[0] == "file" and isinstance(key[2], str)
                and key[2] in _PLACE_FIELD and _whole(key[3])):
            return key
    return None


@dataclass(frozen=True)
class CaptureFilter:
    """What the Learning list shows: captures that contain every word of
    query, come from source (a source_choices key; None for any) and have
    status (a STATUS_FILTERS key)."""
    query: str = ""
    source: tuple = None
    status: str = "all"

    @cached_property
    def words(self):
        return search_text(self.query).split()

    @property
    def active(self):
        return bool(self.words) or self.source is not None or self.status != "all"

    def state(self):
        """JSON-ready form for the workspace file."""
        return {"query": self.query, "source": None if self.source is None else list(self.source),
                "status": self.status}

    @classmethod
    def from_state(cls, value):
        """The filter saved by state(). Each unusable part falls back to its
        default; this never raises."""
        if not isinstance(value, dict):
            return cls()
        query, status = value.get("query"), value.get("status")
        return cls(query=query if isinstance(query, str) else "",
                   source=_saved_source_key(value.get("source")),
                   status=status if isinstance(status, str) and status in STATUS_FILTERS else "all")


def _is_time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False  # a JSON integer can be too large to convert to seconds


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
    if isinstance(card, dict):
        for field in ("due", "last"):
            if field in card and not _is_time(card[field]):
                return f"'{field}' must be a time in seconds"
    problem = scheduler.card_problem(card)
    if problem:
        return problem
    if "due" not in card:  # the Learning list shows it
        return "progress has no due time"
    return None


def learning_problem(data, scheduler=None):
    """Why the content of a learning.json cannot be used, or None. Checks
    everything the app reads from it (module backups use it as well)."""
    scheduler = scheduler or LeitnerScheduler()
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return 'expected {"schema": 1, "items": {...}}'
    if data.get("schema") != 1:
        return f"unsupported version {data.get('schema')!r}"
    for key, item in data["items"].items():
        problem = _capture_problem(key, item, scheduler)
        if problem:
            return f"capture {key!r}: {problem}"
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
        problem = learning_problem(data, self.scheduler)
        if problem:
            raise DataFileError(path, problem)
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

    def matches(self, item, capture_filter, now=None, fields=None):
        """Whether the Learning list shows item under capture_filter. fields
        are search_fields(item), if already known. Due means what due() and
        status() mean by it."""
        status, kind = capture_filter.status, item["kind"]
        if status in ("open", "resolved", "questions") and kind != "question":
            return False
        if status in ("due", "later", "concepts") and kind != "concept":
            return False
        if status in ("open", "resolved") and item["resolved"] != (status == "resolved"):
            return False
        if status in ("due", "later"):
            now = time.time() if now is None else now
            if self.scheduler.is_due(item["card"], now) != (status == "due"):
                return False
        wanted = capture_filter.source
        if wanted is not None:
            document, place = source_keys(item["source"])
            if (document + place if place and len(wanted) == 4 else document) != wanted:
                return False
        if capture_filter.words:
            fields = search_fields(item) if fields is None else fields
            return all(any(word in field for field in fields) for word in capture_filter.words)
        return True

    def status(self, item, now=None):
        if item["kind"] == "question":
            return "Resolved" if item["resolved"] else "Open question"
        now = time.time() if now is None else now
        if self.scheduler.is_due(item["card"], now):
            return "Due now"
        try:
            return "Review " + datetime.fromtimestamp(item["card"]["due"]).strftime("%b %d")
        except (OverflowError, OSError, ValueError):
            return "Review later"
