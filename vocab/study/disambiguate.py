"""Word-sense disambiguation: pick the right definition for a word *in context*.

Problem: WordNet orders senses by corpus frequency, so the first sense is often
wrong for the reader's context (its first sense of "ephemeral" is the mayfly
insect, not "short-lived"). When a word is added from a sentence (e.g. highlighted
while reading), we can do far better than "sense[0]".

This is a pluggable seam (like Dictionary / Scheduler / Judge):

    Disambiguator.rank(word, sentence, senses) -> reordered list[Sense]

Backends:
  * NlpDisambiguator  — zero-cost, no downloads beyond WordNet's own POS tagger:
        1. POS-tag the word in its sentence, keep only senses of that part of
           speech (fixes the "ephemeral"/tense class of error).
        2. Among those, use NLTK's Lesk (gloss/context overlap) to pick the best
           and float it to the front.
  * OllamaDisambiguator — STUB for now: if a local Ollama server is running,
        ask a small model which sense fits; else fall back to NLP. Enabled via
        config.disambiguator = "ollama". Costs nothing to leave off.

get_disambiguator(name) returns the configured backend, always degrading to NLP
(and NLP degrades to "unchanged") so word-adding never breaks.
"""
from __future__ import annotations

import json
import urllib.request
from typing import List, Optional

from ..core.models import Sense

# WordNet single-letter POS codes we map our human-readable Sense.pos back to.
_HUMAN_TO_WN = {"noun": "n", "verb": "v", "adjective": "a", "adverb": "r"}


class Disambiguator:
    id = "base"

    def rank(self, word: str, sentence: str, senses: List[Sense]) -> List[Sense]:
        """Return senses reordered so the best-in-context sense is first."""
        return senses


class NlpDisambiguator(Disambiguator):
    """POS-filter + Lesk. No network, no model download (uses NLTK taggers)."""

    id = "nlp"

    def _ensure_tagger(self) -> bool:
        try:
            import nltk
        except Exception:
            return False
        needed = ["averaged_perceptron_tagger_eng", "averaged_perceptron_tagger",
                  "punkt_tab", "punkt"]
        for res in needed:
            kind = "taggers" if "tagger" in res else "tokenizers"
            try:
                nltk.data.find(f"{kind}/{res}")
            except LookupError:
                try:
                    nltk.download(res, quiet=True)
                except Exception:
                    pass
        return True

    def _wn_pos_of(self, word: str, sentence: str) -> Optional[str]:
        try:
            import nltk
            toks = nltk.word_tokenize(sentence)
            tags = nltk.pos_tag(toks)
        except Exception:
            return None
        tag = None
        wl = word.lower()
        for w, t in tags:
            if w.lower() == wl:
                tag = t
                break
        if not tag:
            return None
        if tag.startswith("J"):
            return "a"
        if tag.startswith("V"):
            return "v"
        if tag.startswith("N"):
            return "n"
        if tag.startswith("R"):
            return "r"
        return None

    def rank(self, word: str, sentence: str, senses: List[Sense]) -> List[Sense]:
        if not sentence or len(senses) <= 1:
            return senses
        if not self._ensure_tagger():
            return senses
        target_wn = self._wn_pos_of(word, sentence)

        # 1) POS filter: senses whose part of speech matches the usage.
        if target_wn:
            target_human = {"n": "noun", "v": "verb", "a": "adjective",
                            "r": "adverb"}[target_wn]
            matching = [s for s in senses if (s.pos or "").lower() == target_human]
            others = [s for s in senses if (s.pos or "").lower() != target_human]
        else:
            matching, others = list(senses), []

        pool = matching or list(senses)

        # 2) Lesk among the POS-matching pool: overlap of sentence words with
        #    each sense's definition+example; float the best match to front.
        best_i = self._lesk_index(sentence, pool)
        if best_i > 0:
            pool.insert(0, pool.pop(best_i))

        return pool + others

    @staticmethod
    def _lesk_index(sentence: str, senses: List[Sense]) -> int:
        """Return index of the sense whose gloss best overlaps the sentence."""
        import re
        stop = {"the", "a", "an", "of", "to", "and", "in", "is", "was", "were",
                "that", "this", "it", "he", "she", "they", "his", "her", "with",
                "for", "on", "at", "as", "by", "be", "are", "or", "but"}
        ctx = {w for w in re.findall(r"[a-z]+", sentence.lower()) if w not in stop}
        best_i, best_score = 0, -1
        for i, s in enumerate(senses):
            gloss = f"{s.definition} {s.example}".lower()
            words = {w for w in re.findall(r"[a-z]+", gloss) if w not in stop}
            score = len(ctx & words)
            if score > best_score:
                best_i, best_score = i, score
        return best_i


class OllamaDisambiguator(Disambiguator):
    """Ask a local Ollama model which sense fits; fall back to NLP if unavailable.

    Costs nothing unless the user installs Ollama and enables this backend.
    """

    id = "ollama"
    # Use 127.0.0.1, NOT "localhost": on Windows "localhost" resolves to ::1
    # (IPv6) first, and since Ollama listens on IPv4 the connect stalls ~1s
    # waiting for the IPv6 attempt to fail. 127.0.0.1 is ~2ms.
    HOST = "http://127.0.0.1:11434"
    ENDPOINT = "http://127.0.0.1:11434/api/generate"
    _up_cache = None          # (timestamp, bool) shared across instances
    _UP_TTL = 5.0             # seconds a server-up result stays fresh

    def __init__(self, model: str = "llama3.2:3b", fallback: Optional[Disambiguator] = None):
        self.model = model
        self.fallback = fallback or NlpDisambiguator()

    def _server_up(self, use_cache: bool = True) -> bool:
        import time as _t
        if use_cache and OllamaDisambiguator._up_cache is not None:
            ts, val = OllamaDisambiguator._up_cache
            if _t.time() - ts < self._UP_TTL:
                return val
        try:
            with urllib.request.urlopen(f"{self.HOST}/api/tags", timeout=1) as r:
                r.read()
            up = True
        except Exception:
            up = False
        OllamaDisambiguator._up_cache = (_t.time(), up)
        return up

    def rank(self, word: str, sentence: str, senses: List[Sense]) -> List[Sense]:
        if not sentence or len(senses) <= 1 or not self._server_up():
            return self.fallback.rank(word, sentence, senses)
        numbered = "\n".join(f"{i}. ({s.pos}) {s.definition}" for i, s in enumerate(senses))
        prompt = (
            f"Sentence: \"{sentence}\"\n"
            f"Which numbered definition of the word \"{word}\" best fits how it is "
            f"used in that sentence? Reply with ONLY the number.\n\n{numbered}\n\nNumber:"
        )
        try:
            body = json.dumps({"model": self.model, "prompt": prompt,
                               "stream": False, "options": {"temperature": 0}}).encode()
            req = urllib.request.Request(self.ENDPOINT, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                resp = json.load(r).get("response", "")
            import re
            m = re.search(r"\d+", resp)
            if m:
                idx = int(m.group())
                if 0 <= idx < len(senses):
                    out = list(senses)
                    out.insert(0, out.pop(idx))
                    return out
        except Exception:
            pass
        return self.fallback.rank(word, sentence, senses)


_BACKENDS = {}


def _register(inst: Disambiguator) -> None:
    _BACKENDS[inst.id] = inst


_register(Disambiguator())      # "base" = no-op
_register(NlpDisambiguator())   # "nlp"  = zero-cost default


def get_disambiguator(name: str = "nlp", model: str = "llama3.2:3b") -> Disambiguator:
    name = (name or "nlp").lower()
    if name == "ollama":
        # constructed on demand so importing this module never needs a server
        return OllamaDisambiguator(model=model, fallback=_BACKENDS["nlp"])
    return _BACKENDS.get(name, _BACKENDS["nlp"])
