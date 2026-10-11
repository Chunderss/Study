"""Text comparison rules shared by answer grading and Learning search."""
from __future__ import annotations

import re
import unicodedata

# Latin letters that have no accent to split off, compared as their base
# letters, and the Catalan middle dot (col·lecció; NFKC writes ŀ as l·),
# compared as nothing.
_LATIN_BASE = str.maketrans({"ł": "l", "ø": "o", "đ": "d", "ħ": "h", "ŧ": "t",
                             "ı": "i", "æ": "ae", "œ": "oe", "\u00b7": ""})
# Translating a long text costs more than searching it for those letters.
_HAS_LATIN_BASE = re.compile("[" + re.escape("".join(map(chr, _LATIN_BASE))) + "]")
# A run of non-ASCII characters that follows an ASCII one. Combining marks at
# its start are written on that ASCII (Latin) letter.
_AFTER_ASCII = re.compile(r"(?<=[\x00-\x7f])[^\x00-\x7f]+")


def _drop_leading_marks(match):
    run = match.group()
    start = 0
    while start < len(run) and unicodedata.combining(run[start]):
        start += 1
    return run[start:]


def drop_latin_accents(text: str) -> str:
    """Drop accents from Latin letters ('café' -> 'cafe', 'żółty' -> 'zolty',
    'æther' -> 'aether'); other scripts keep theirs (й, ё, が). text is
    case-folded."""
    text = unicodedata.normalize("NFD", text)
    if _HAS_LATIN_BASE.search(text):
        text = text.translate(_LATIN_BASE)
    return unicodedata.normalize("NFC", _AFTER_ASCII.sub(_drop_leading_marks, text))
