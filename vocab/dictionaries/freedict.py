"""Free Dictionary API backend (dictionaryapi.dev).

Online, no API key, rich prose definitions with multiple senses. During
development the endpoint proved flaky (intermittent 403s without a User-Agent,
and read timeouts), so this backend is deliberately defensive:

    * sends a real User-Agent (bare urllib gets 403'd)
    * short connect/read timeout
    * a couple of retries with backoff
    * raises LookupFailed(recoverable=True) on transient errors so the CLI can
      offer a manual-definition fallback instead of crashing.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List

from ..core.models import Sense
from .base import Dictionary, LookupFailed

_ENDPOINT = "https://api.dictionaryapi.dev/api/v2/entries/en/"
_UA = "VocabStudy/0.1 (offline-first vocab trainer)"


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
                return self._parse(data)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise LookupFailed(f"'{word}' not found.", recoverable=False)
                last_exc = e
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
                last_exc = e
            if attempt < self.retries:
                time.sleep(0.6 * (attempt + 1))
        raise LookupFailed(
            f"Could not reach the dictionary ({type(last_exc).__name__}). "
            f"Check your connection or add a definition manually.",
            recoverable=True,
        )

    @staticmethod
    def _parse(data) -> List[Sense]:
        senses: List[Sense] = []
        for entry in data if isinstance(data, list) else []:
            for meaning in entry.get("meanings", []):
                pos = meaning.get("partOfSpeech", "")
                for d in meaning.get("definitions", []):
                    text = (d.get("definition") or "").strip()
                    if text:
                        senses.append(Sense(
                            definition=text, pos=pos,
                            example=(d.get("example") or "").strip(),
                        ))
        if not senses:
            raise LookupFailed("No definitions returned.", recoverable=False)
        return senses[:6]  # cap to keep lists tidy
