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
    TEST = "test"
    MIDSEM = "midsem"
    COMPRE = "compre"
    LAB_COMPRE = "lab_compre"
    LAB_QUIZ = "lab_quiz"
    NONE = "none"
    UNKNOWN = "unknown"


class DocType(StrEnum):
    """What kind of material a document is."""

    PYQ = "pyq"
    SOLUTION = "solution"
    NOTES = "notes"
    SLIDES = "slides"
    TUTORIAL = "tutorial"
    ASSIGNMENT = "assignment"
    LAB = "lab"
    CHEATSHEET = "cheatsheet"
    HANDOUT = "handout"
    TEXTBOOK = "textbook"
    GRADE_STATS = "grade_stats"
    OTHER = "other"
    UNKNOWN = "unknown"


class LinkStatus(StrEnum):
    """Last known health of a document's Drive link."""

    UNKNOWN = "unknown"
    OK = "ok"
    BROKEN = "broken"
    NO_ACCESS = "no_access"


class ExtractionMethod(StrEnum):
    """How a document's metadata was produced."""

    RULE = "rule"
    LLM = "llm"
    MANUAL = "manual"


class SyllabusScope(StrEnum):
    """Which part of the course a lecture file covers, when the drive says so."""

    PRE_MIDSEM = "pre_midsem"
    POST_MIDSEM = "post_midsem"
