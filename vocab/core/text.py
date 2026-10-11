"""Text comparison rules for answer grading and Learning search."""
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
# Typeset apostrophes, quotation marks, hyphens and dashes, as the ASCII
# character a keyboard types for them; the soft hyphen, which only marks
# where a word may break, is dropped. The apostrophe look-alikes include the
# ones grading accepts in "n't". Meant for NFKC text, in which the
# non-breaking hyphen is already U+2010 and fullwidth forms are ASCII.
_TYPED = {**dict.fromkeys("\u2018\u2019\u201a\u201b\u02bc\u2032", "'"),  # ‘ ’ ‚ ‛ ʼ ′
          **dict.fromkeys("\u201c\u201d\u201e\u201f", '"'),               # “ ” „ ‟
          **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
          "\u00ad": ""}
# Substituting the few matches is quicker than translating a long passage.
_TYPESET = re.compile("[" + "".join(_TYPED) + "]")


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


def typed_punctuation(text: str) -> str:
    """text with typeset punctuation as a keyboard types it: 'don’t' ->
    "don't", '“well‐known”' -> '"well-known"', 'hy\\u00adphen' -> 'hyphen'."""
    return _TYPESET.sub(lambda match: _TYPED[match.group()], text)
