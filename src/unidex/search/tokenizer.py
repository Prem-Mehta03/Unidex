"""Turn text into search tokens.

The same function is used for documents (when building the index) and for
queries (when searching). That symmetry is what makes matching work: whatever
a document word becomes, the same word typed in a query becomes the same thing.

Steps, in order:

1. Fold case and strip accents.
2. Split into runs of letters and digits (punctuation, spaces and underscores
   separate runs), so ``"Midsem_Solution.pdf"`` gives ``midsem solution pdf``.
3. Split a long word glued to a number: ``"Lecture06"`` gives ``lecture 06``.
   Short prefixes are left alone because they are usually course codes
   (``f213``, ``m3``, ``cs1``).
4. Drop leading zeros from numbers (``06`` becomes ``6``), so "lecture 6" finds it.
5. Drop very common words (``the``, ``of``, ...).
6. Strip a plural ``s`` (``slides`` becomes ``slide``). This is a deliberately
   tiny stand-in for stemming.
"""

import re
import unicodedata

STOPWORDS = frozenset({"a", "an", "and", "the", "of", "in", "on", "for", "to", "with"})

# A letters-only prefix at least this long, followed by digits, is split apart.
MIN_WORD_LETTERS_BEFORE_DIGITS = 4

_RUN = re.compile(r"[a-z0-9]+")
_WORD_THEN_DIGITS = re.compile(rf"^([a-z]{{{MIN_WORD_LETTERS_BEFORE_DIGITS},}})(\d+)$")
_MIN_LENGTH_FOR_PLURAL_STRIP = 4


def tokenize(text: str, *, stem: bool = True) -> list[str]:
    """Split text into normalised tokens.

    Args:
        text: Any text: a file name, a folder path or a student's query.
        stem: Whether to strip a plural ``s``. Autocomplete turns this off so
            it can suggest real words ("slides") instead of stems ("slide").

    Returns:
        Tokens in order of appearance. Duplicates are kept, because how often a
        word occurs matters for ranking.
    """
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))

    tokens: list[str] = []
    for run in _RUN.findall(folded):
        for piece in _split_word_and_digits(run):
            if piece in STOPWORDS:
                continue
            if piece.isdigit():
                piece = piece.lstrip("0") or "0"
            elif stem:
                piece = _strip_plural(piece)
            tokens.append(piece)
    return tokens


def _split_word_and_digits(run: str) -> list[str]:
    """Split ``"lecture06"`` into ``["lecture", "06"]``; leave other runs whole."""
    match = _WORD_THEN_DIGITS.match(run)
    if match is None:
        return [run]
    return [match.group(1), match.group(2)]


def _strip_plural(word: str) -> str:
    """Remove a trailing plural ``s`` from longer alphabetic words."""
    if (
        word.isalpha()
        and len(word) >= _MIN_LENGTH_FOR_PLURAL_STRIP
        and word.endswith("s")
        and not word.endswith("ss")
    ):
        return word[:-1]
    return word
