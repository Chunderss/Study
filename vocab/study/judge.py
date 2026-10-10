"""Answer judging for mechanical-repetition mode.

Interface: score(user_answer, reference) -> JudgeResult. The judge gives the
verdict (passed) and words its feedback from the same rule, so the two cannot
disagree; the study session records a pass as Review.GOOD and anything else as
Review.AGAIN. A reference the judge cannot grade (gradable=False) is not
recorded at all.

KeywordJudge (MVP): tokenises both texts, drops stopwords, matches regular
English inflections, and computes the share of the reference's keywords that
the user recalled; an answer passes with at least PASS_SCORE of them. This is
deterministic, instant, and needs no downloads — good enough to gate promotion
while the semantic judges (embeddings/ollama) remain optional.

Keywords leave out stopwords and words of one or two letters. A definition
with nothing else is compared on its short words ("an ox"), then on all its
words; one with no words at all cannot be graded.

Both texts are read as Unicode words after NFKC normalisation and case
folding. Accents on Latin letters are optional (cafe = café); marks in other
scripts count (и is not й, е is not ё, か is not が). Chinese and Japanese
are not split into words, so only whole matching words count there.
"""
from __future__ import annotations

import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Set

# Share of the reference's keywords an answer must recall to pass.
PASS_SCORE = 0.5

_STOP = {
    "a", "an", "the", "of", "to", "and", "or", "in", "on", "at", "for", "with",
    "is", "are", "was", "were", "be", "been", "being", "as", "by", "that",
    "this", "it", "its", "from", "which", "who", "whom", "something", "someone",
    "used", "esp", "especially", "etc", "eg", "ie", "not", "no", "any", "some",
}


def _scan(text: str) -> List[str]:
    """Runs of letters and digits, with the combining marks written on them."""
    words: List[str] = []
    word: List[str] = []
    for ch in text:
        if ch.isalnum() or (word and unicodedata.category(ch).startswith("M")):
            word.append(ch)
        elif word:
            words.append("".join(word))
            word = []
    if word:
        words.append("".join(word))
    return words


def _plain(word: str) -> str:
    """Drop accents from Latin letters ('café' -> 'cafe'); other scripts keep theirs."""
    out: List[str] = []
    for ch in unicodedata.normalize("NFD", word):
        if unicodedata.combining(ch) and out and out[-1].isascii():
            continue
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out))


def _keywords(text: str, tier: int) -> Dict[str, str]:
    """Keywords in order: compared form -> the word as first written.

    Tier 1 leaves out stopwords and words of 1-2 letters, tier 2 keeps the
    short words, and tier 3 keeps every word.
    """
    out: Dict[str, str] = {}
    for w in _scan(unicodedata.normalize("NFKC", text or "")):
        low = _plain(w.casefold())
        if (tier < 3 and low in _STOP) or (tier < 2 and len(low) <= 2):
            continue
        out.setdefault(_stem(low), w)
    return out


# ---- stemming ----------------------------------------------------------------
# The inflectional steps of Porter's algorithm (1a-1c, 5), built in so the base
# CLI and the desktop app grade alike without NLTK. Changes for short words:
# ties/tie and tied/tie, -xes/-ches/-shes, a final s kept after a lone vowel
# (gas, yes) and in -ss/-us (glass, virus), -se like -s (gases/gas, cases/case),
# one letter of a final double consonant (add/added, stuff/stuffed), -ly
# adverbs (happily/happy) and a few exceptions. Full Porter merges too much
# (organ/organize, general/generous). Irregular forms (made/make) stay apart.
_STEM_EXCEPTIONS = {"sky": "sky", "skies": "sky", "skis": "ski", "dying": "die",
                    "lying": "lie", "tying": "tie", "early": "earli"}


def _cons(w: str, i: int) -> bool:
    if w[i] in "aeiou":
        return False
    if w[i] == "y":
        return i == 0 or not _cons(w, i - 1)
    return True


def _measure(s: str) -> int:
    m, vowel = 0, False
    for i in range(len(s)):
        v = not _cons(s, i)
        m += vowel and not v
        vowel = v
    return m


def _has_vowel(s: str) -> bool:
    return any(not _cons(s, i) for i in range(len(s)))


def _cvc(s: str) -> bool:
    return (len(s) >= 3 and _cons(s, len(s) - 3) and not _cons(s, len(s) - 2)
            and _cons(s, len(s) - 1) and s[-1] not in "wxy")


def _double(s: str) -> bool:
    return len(s) >= 2 and s[-1] == s[-2] and _cons(s, len(s) - 1)


def _undo_ed_ing(w: str) -> str:
    for suffix in ("ed", "ing"):
        if w.endswith(suffix) and _has_vowel(w[:-len(suffix)]):
            w = w[:-len(suffix)]
            if w.endswith(("at", "bl", "iz")):
                return w + "e"
            if _double(w) and w[-1] not in "lsz":
                return w[:-1]
            if _measure(w) == 1 and _cvc(w):
                return w + "e"
            return w
    return w


def _stem(w: str) -> str:
    """Map regular inflections of an ASCII word to one form (make/makes/making)."""
    if w in _STEM_EXCEPTIONS:
        return _STEM_EXCEPTIONS[w]
    if len(w) <= 2 or not (w.isascii() and w.isalpha()):
        return w
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith(("ies", "ied")):
        w = w[:-1] if len(w) == 4 else w[:-2]          # ties -> tie, cries -> cri
    elif w.endswith(("xes", "ches", "shes", "zzes")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us")) and _has_vowel(w[:-2]):
        w = w[:-1]                                      # gas, yes keep their s
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    else:
        w = _undo_ed_ing(w)
    if w.endswith("y") and len(w) > 2 and _cons(w, len(w) - 2):
        w = w[:-1] + "i"                                # cry/cried, happy/happily
    # -ly adverbs: happily/happy, usually/usual, knowingly/know (not apply)
    if w.endswith("li") and len(w) >= 5 and _has_vowel(w[:-2]) and not _double(w[:-2]):
        w = _undo_ed_ing(w[:-2])
    if w.endswith("e"):
        s = w[:-1]
        if _measure(s) > 1 or (_measure(s) == 1 and not _cvc(s)) or s.endswith("s"):
            w = s                                       # case/cases, gas/gases
    if w.endswith("ll"):
        if _measure(w) > 1:
            w = w[:-1]
    elif _double(w) and w[-1] not in "sz":
        w = w[:-1]                                      # add/added, stuff/stuffed
    return w


@dataclass
class JudgeResult:
    score: float          # 0..1
    feedback: str         # states the verdict
    missed: Set[str]      # reference keywords not mentioned, as written
    passed: bool          # the verdict the study session records
    gradable: bool = True  # False: the reference has no words to compare with


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
        for tier in (1, 2, 3):
            ref = _keywords(reference, tier)
            if ref:
                break
        else:
            return JudgeResult(0.0, "This card's definition has no words to compare "
                                    "with, so it cannot be graded here.", set(),
                               passed=False, gradable=False)
        ans = _keywords(user_answer, tier)
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
