"""Judge whether text read from a file is good enough to search.

Handwriting, bad scans and maths often come out of PDF readers as run-together words and
stray symbols ("Q1)ProofbyStructuralInduction"). Searching such text finds nothing useful, so
we score it and ignore the poor ones. The score is the share of "word-like" tokens: a short
word made of letters, or a number. Clean prose scores 0.8 to 1.0, garbled handwriting 0.3 to 0.6.
"""

import re

MIN_QUALITY = 0.65

_WORD = re.compile(r"^[A-Za-z][A-Za-z'\u2019-]{0,14}$")
_NUMBER = re.compile(r"^[\d.,/:%+\-=()<>]+$")
_TRIM = ".,;:?!()[]{}\"'\u201c\u201d"


def text_quality(text: str) -> float:
    """Score how readable extracted text is.

    Args:
        text: Text read from a file.

    Returns:
        A number from 0 (garbage or empty) to 1 (clean).
    """
    tokens = [t for t in (raw.strip(_TRIM) for raw in text.split()) if t]
    if not tokens:
        return 0.0
    good = sum(1 for t in tokens if _WORD.match(t) or _NUMBER.match(t))
    return good / len(tokens)
