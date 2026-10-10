"""Answer judging for mechanical-repetition mode.

Interface: score(user_answer, reference) -> JudgeResult. The judge gives the
verdict (passed) and words its feedback from the same rule, so the two cannot
disagree; the study session records a pass as Review.GOOD and anything else as
Review.AGAIN.

KeywordJudge (MVP): tokenises both texts, drops stopwords, stems crudely, and
computes the share of the reference's keywords that the user recalled; an
answer passes with at least PASS_SCORE of them. This is deterministic,
instant, and needs no downloads — good enough to gate promotion while the
semantic judges (embeddings/ollama) remain optional.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Set

# Share of the reference's keywords an answer must recall to pass.
PASS_SCORE = 0.5

_STOP = {
    "a", "an", "the", "of", "to", "and", "or", "in", "on", "at", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "as", "by", "that",
    "this", "it", "its", "from", "which", "who", "whom", "something", "someone",
    "used", "esp", "especially", "etc", "eg", "ie", "not", "no", "any", "some",
}


def _keywords(text: str) -> Dict[str, str]:
    """Keywords in order: compared form -> the word as first written."""
    out: Dict[str, str] = {}
    for w in re.findall(r"[A-Za-z]+", text or ""):
        low = w.lower()
        if low in _STOP or len(low) <= 2:
            continue
        out.setdefault(_stem(low), w)
    return out


def _stem(w: str) -> str:
    for suf in ("ingly", "edly", "ing", "edly", "ed", "es", "ly", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


@dataclass
class JudgeResult:
    score: float          # 0..1
    feedback: str         # states the verdict
    missed: Set[str]      # reference keywords not mentioned, as written
    passed: bool          # the verdict the study session records


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
        ref = _keywords(reference)
        ans = _keywords(user_answer)
        if not ref:
            # No content words to match (rare) -> accept any non-empty answer.
            ok = bool(ans)
            return JudgeResult(1.0 if ok else 0.0,
                               "Accepted." if ok else "Empty answer.", set(), passed=ok)
        missed = [word for key, word in ref.items() if key not in ans]
        hit = len(ref) - len(missed)
        passed = hit / len(ref) >= PASS_SCORE
        if not missed:
            fb = "Accepted — you recalled every key term."
        elif passed:
            fb = "Accepted — you recalled enough key terms."
        else:
            fb = f"Not accepted — at least {PASS_SCORE:.0%} of the key terms are needed."
        if missed:
            fb += " Key terms not mentioned: " + ", ".join(missed[:6]) + "."
        return JudgeResult(round(hit / len(ref), 3), fb, set(missed), passed=passed)


JUDGES: Dict[str, Judge] = {j.id: j for j in [KeywordJudge()]}


def get_judge(judge_id: str) -> Judge:
    try:
        return JUDGES[judge_id]
    except KeyError:
        raise KeyError(f"Unknown judge '{judge_id}'. Known: {', '.join(JUDGES)}")
