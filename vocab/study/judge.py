"""Answer judging for mechanical-repetition mode.

Interface: score(user_answer, reference) -> JudgeResult(score 0..1, feedback).
The CLI maps score >= threshold to Review.GOOD.

KeywordJudge (MVP): tokenises both texts, drops stopwords, stems crudely, and
computes overlap of the reference's content words that the user recalled. This
is deterministic, instant, and needs no downloads — good enough to gate
promotion while the semantic judges (embeddings/ollama) remain optional.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Set

_STOP = {
    "a", "an", "the", "of", "to", "and", "or", "in", "on", "at", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "as", "by", "that",
    "this", "it", "its", "from", "which", "who", "whom", "something", "someone",
    "used", "esp", "especially", "etc", "eg", "ie", "not", "no", "any", "some",
}


def _tokens(text: str) -> Set[str]:
    words = re.findall(r"[a-z]+", (text or "").lower())
    out = set()
    for w in words:
        if w in _STOP or len(w) <= 2:
            continue
        out.add(_stem(w))
    return out


def _stem(w: str) -> str:
    for suf in ("ingly", "edly", "ing", "edly", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


@dataclass
class JudgeResult:
    score: float          # 0..1
    feedback: str
    missed: Set[str]      # reference keywords the user did not recall


class Judge(ABC):
    id: str = ""
    name: str = ""

    @abstractmethod
    def score(self, user_answer: str, reference: str) -> JudgeResult:
        ...


class KeywordJudge(Judge):
    id = "keyword"
    name = "Keyword overlap (offline, no download)"

    def score(self, user_answer: str, reference: str) -> JudgeResult:
        ref = _tokens(reference)
        ans = _tokens(user_answer)
        if not ref:
            # No content words to match (rare) -> accept any non-empty answer.
            ok = bool(ans)
            return JudgeResult(1.0 if ok else 0.0,
                               "Accepted." if ok else "Empty answer.", set())
        hit = ref & ans
        missed = ref - ans
        score = len(hit) / len(ref)
        if score >= 0.6:
            fb = "Good — you captured the key ideas."
        elif score >= 0.3:
            fb = "Partial — you got some of it."
        else:
            fb = "Missed most of the definition."
        if missed:
            fb += " Key terms not mentioned: " + ", ".join(sorted(missed)[:6]) + "."
        return JudgeResult(round(score, 3), fb, missed)


JUDGES: Dict[str, Judge] = {j.id: j for j in [KeywordJudge()]}


def get_judge(judge_id: str) -> Judge:
    try:
        return JUDGES[judge_id]
    except KeyError:
        raise KeyError(f"Unknown judge '{judge_id}'. Known: {', '.join(JUDGES)}")
