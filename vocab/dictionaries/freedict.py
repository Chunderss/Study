"""Free Dictionary API backend (dictionaryapi.dev).

Online, no API key, rich prose definitions with multiple senses. During
development the endpoint proved flaky (intermittent 403s without a User-Agent,
and read timeouts), so this backend is deliberately defensive:

    * sends a real User-Agent (bare urllib gets 403'd)
    * short connect/read timeout
    * a couple of retries with backoff, for network errors and for a reply
      that arrives truncated or garbled (never for our own errors)
    * raises LookupFailed(recoverable=True) on transient errors so the CLI can
      offer a manual-definition fallback instead of crashing.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List

from ..core.models import Sense
from ..version import __version__
from .base import Dictionary, LookupFailed

_ENDPOINT = "https://api.dictionaryapi.dev/api/v2/entries/en/"
_UA = f"VocabStudy/{__version__} (offline-first vocab trainer)"


class FreeDictionary(Dictionary):
    id = "freedict"
    name = "Free Dictionary API"
    description = "Online English dictionary (dictionaryapi.dev). No key, no install."
    install_cost = ""             # nothing to download
    requires_network = True

    def __init__(self, timeout: float = 8.0, retries: int = 2):
        self.timeout = timeout
        self.retries = retries

    def available(self) -> bool:
        return True  # always installable; network is checked at lookup time

    def install(self) -> None:
        return  # nothing to do

    def lookup(self, word: str) -> List[Sense]:
        word = word.strip()
        if not word:
            raise LookupFailed("Empty word.")
        url = _ENDPOINT + urllib.parse.quote(word)
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.load(resp)
                break
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise LookupFailed(f"'{word}' not found.", recoverable=False)
                last_exc = e
            # Network failures (OSError covers URLError, timeouts and resets),
            # and a reply that arrived truncated or garbled (IncompleteRead,
            # BadStatusLine, bytes that are not JSON text): another attempt may
            # get a whole one. Anything else is our own error and is raised.
            except (OSError, http.client.HTTPException,
                    json.JSONDecodeError, UnicodeDecodeError) as e:
                last_exc = e
            if attempt < self.retries:
                time.sleep(0.6 * (attempt + 1))
        else:
            raise LookupFailed(
                f"Could not reach the dictionary ({type(last_exc).__name__}). "
                f"Check your connection or add a definition manually.",
                recoverable=True,
            )
        return self._parse(data)  # a well-formed reply is not fetched again

    @staticmethod
    def _parse(data) -> List[Sense]:
        # Valid JSON in a shape the service does not document is its problem,
        # not proof that the word does not exist.
        unexpected = LookupFailed(
            "The dictionary sent a reply in an unexpected format. "
            "Try again later or add a definition manually.", recoverable=True)
        if not isinstance(data, list):
            raise unexpected
        senses: List[Sense] = []
        for entry in data:
            meanings = entry.get("meanings", []) if isinstance(entry, dict) else None
            if not isinstance(meanings, list):
                raise unexpected
            for meaning in meanings:
                definitions = meaning.get("definitions", []) if isinstance(meaning, dict) else None
                if not isinstance(definitions, list):
                    raise unexpected
                pos = meaning.get("partOfSpeech")
                for d in definitions:
                    if not isinstance(d, dict):
                        raise unexpected
                    text, example = d.get("definition"), d.get("example")
                    text = text.strip() if isinstance(text, str) else ""
                    if text:
                        senses.append(Sense(
                            definition=text, pos=pos if isinstance(pos, str) else "",
                            example=example.strip() if isinstance(example, str) else "",
                        ))
        if not senses:
            raise LookupFailed("No definitions returned.", recoverable=False)
        return senses  # all of them: the caller ranks, then trims
