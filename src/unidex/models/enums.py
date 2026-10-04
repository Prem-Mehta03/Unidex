"""Controlled vocabularies for document metadata.

These values are mirrored in ``CHECK`` constraints in ``db/schema.sql`` so the
database refuses anything outside the list. A test keeps the two in sync.

Note the difference between ``NONE`` and ``UNKNOWN``:

* ``NONE`` means "not applicable" (a lecture slide deck has no exam type).
* ``UNKNOWN`` means "applicable, but we could not work it out".
"""

from enum import StrEnum


class ExamType(StrEnum):
    """Which evaluation a document belongs to."""

    QUIZ = "quiz"
    MIDSEM = "midsem"
    COMPRE = "compre"
    LAB_COMPRE = "lab_compre"
    NONE = "none"
    UNKNOWN = "unknown"


class DocType(StrEnum):
    """What kind of material a document is."""

    PYQ = "pyq"
    SOLUTION = "solution"
    NOTES = "notes"
    SLIDES = "slides"
    TUTORIAL = "tutorial"
    LAB = "lab"
    CHEATSHEET = "cheatsheet"
    OTHER = "other"
    UNKNOWN = "unknown"


class LinkStatus(StrEnum):
    """Last known health of a document's Drive link."""

    UNKNOWN = "unknown"
    OK = "ok"
    BROKEN = "broken"
    NO_ACCESS = "no_access"
