"""The NLTK data Vocab Study uses, and an offline check of the tagger.

The runtime, tools/build_desktop.py and VocabStudy.spec all read this module,
so it imports nothing else from vocab, and imports nltk only when asked.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

#: NLTK's English part-of-speech tagger (NLTK 3.9+). Sense selection tags one
#: sentence without Punkt, so no tokenizer data is needed.
POS_TAGGER = "averaged_perceptron_tagger_eng"

#: NLTK data package -> the zip the desktop build bundles. NLTK reads both
#: zips in place, so nothing else is shipped.
NLTK_PACKAGES = {
    "wordnet": "corpora/wordnet.zip",
    POS_TAGGER: f"taggers/{POS_TAGGER}.zip",
}


class TaggerProblem(NamedTuple):
    """Why part-of-speech tagging does not work."""

    reason: str
    #: "no-nltk" (nltk cannot be imported), "no-data" (the tagger is not
    #: installed) or "unusable" (installed, but NLTK cannot read it).
    kind: str


_tagger_works = False


def pos_tagger_problem() -> Optional[TaggerProblem]:
    """None if part-of-speech tagging works offline, else why not.

    It tags a word, so missing and unreadable data are both caught. It never
    downloads anything, and only remembers success.
    """
    global _tagger_works
    if _tagger_works:
        return None
    try:
        import nltk
    except ImportError:
        return TaggerProblem("nltk is not installed", "no-nltk")
    except Exception as error:  # installed but broken
        return TaggerProblem(f"nltk cannot be loaded ({type(error).__name__}: {error})",
                             "no-nltk")
    try:
        nltk.pos_tag(["flag"])
    except LookupError:
        return TaggerProblem(f"the NLTK data package '{POS_TAGGER}' is not installed",
                             "no-data")
    except Exception as error:  # e.g. NLTK refusing a hard-linked data file
        return TaggerProblem(f"the NLTK data package '{POS_TAGGER}' cannot be used "
                             f"({type(error).__name__}: {error})", "unusable")
    _tagger_works = True
    return None
