"""Group individual results into "stacks" (one card per kind of material).

A search can match dozens of files. Showing them as a flat list buries the
answer, so files that belong together share a stack, for example "Object
Oriented Programming · Midsem papers" holding the midsem papers (and their
solutions) from every year. The best-matching stack comes first and the student
opens only the stacks they care about.

Rule: two hits share a stack when they have the same course and the same
*kind*. Past papers and their solutions count as one kind per exam type
(a midsem paper and its solution belong together); every other document type
is its own kind.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from unidex.search.catalog import Hit
from unidex.search.labels import DOC_TYPE_LABELS, EXAM_TYPE_LABELS, academic_year_label

PAPER_TYPES = frozenset({"pyq", "solution"})
NO_COURSE_TITLE = "Other files"
# Stacks with equal scores (filter-only browsing) are ordered by kind, papers first.
_KIND_ORDER = ("papers", "tutorial", "notes", "slides", "assignment", "lab", "cheatsheet")


@dataclass(frozen=True, slots=True)
class Stack:
    """A group of related hits.

    Attributes:
        key: Stable identifier, e.g. ``"CS F213|papers|midsem"``.
        title: Heading shown on the card, e.g. ``"Object Oriented Programming · Midsem papers"``.
        course_code: Course code, or empty.
        kind: ``papers`` or a document type.
        exam_type: Exam type for paper stacks, otherwise empty.
        hits: The files, in display order.
        best_score: Highest BM25 score among the hits.
        years: Academic years present, oldest first.
        solution_count: How many files in the stack are solutions or include them.
    """

    key: str
    title: str
    course_code: str
    kind: str
    exam_type: str
    hits: tuple[Hit, ...]
    best_score: float
    years: tuple[int, ...]
    solution_count: int

    @property
    def year_span(self) -> str:
        """Return the years covered, e.g. ``"2019-20 to 2023-24"``, or empty."""
        if not self.years:
            return ""
        first, last = academic_year_label(self.years[0]), academic_year_label(self.years[-1])
        return first if first == last else f"{first} to {last}"


def _kind_of(hit: Hit) -> tuple[str, str]:
    """Return ``(kind, exam_type)`` for a hit."""
    doc = hit.doc
    if doc.doc_type in PAPER_TYPES:
        # "none" and "unknown" both mean "we don't know which exam": one shared stack.
        return "papers", doc.exam_type if doc.exam_type in EXAM_TYPE_LABELS else ""
    return doc.doc_type, ""


def _kind_label(kind: str, exam_type: str) -> str:
    """Return the words after the course name in a stack title."""
    if kind == "papers":
        exam = EXAM_TYPE_LABELS.get(exam_type)
        return f"{exam} papers" if exam else "Exam papers"
    return DOC_TYPE_LABELS.get(kind, kind)


def _hit_order(kind: str, hit: Hit) -> tuple[float, float, str]:
    """Sort key inside a stack.

    Papers read newest first; other material reads best match first.
    """
    year = hit.doc.academic_year or 0
    if kind == "papers":
        return (-year, -hit.score, hit.doc.name)
    return (-hit.score, -year, hit.doc.name)


def _sorted_members(kind: str, members: list[Hit]) -> list[Hit]:
    """Return the hits of one stack in display order."""
    return sorted(members, key=lambda hit: _hit_order(kind, hit))


def group_into_stacks(hits: Iterable[Hit]) -> list[Stack]:
    """Group hits into stacks and order the stacks.

    Args:
        hits: Search results, best first.

    Returns:
        Stacks, best score first. Ties (filter-only browsing) put papers first,
        then other kinds in a fixed order, then A-Z by title.
    """
    buckets: dict[tuple[str, str, str], list[Hit]] = {}
    for hit in hits:
        kind, exam_type = _kind_of(hit)
        buckets.setdefault((hit.doc.course_code, kind, exam_type), []).append(hit)

    stacks: list[Stack] = []
    for (course_code, kind, exam_type), members in buckets.items():
        members = _sorted_members(kind, members)
        course = members[0].doc.course_name or course_code or NO_COURSE_TITLE
        label = _kind_label(kind, exam_type)
        title = f"{course} · {label}" if course_code else label
        years = sorted({m.doc.academic_year for m in members if m.doc.academic_year is not None})
        stacks.append(
            Stack(
                key=f"{course_code}|{kind}|{exam_type}",
                title=title,
                course_code=course_code,
                kind=kind,
                exam_type=exam_type,
                hits=tuple(members),
                best_score=max(m.score for m in members),
                years=tuple(years),
                solution_count=sum(
                    1 for m in members if m.doc.doc_type == "solution" or m.doc.has_solution
                ),
            )
        )
    stacks.sort(key=_stack_order)
    return stacks


def _stack_order(stack: Stack) -> tuple[float, int, str]:
    """Sort key for stacks: best score first, then kind order, then title."""
    kind_rank = _KIND_ORDER.index(stack.kind) if stack.kind in _KIND_ORDER else len(_KIND_ORDER)
    return (-stack.best_score, kind_rank, stack.title)
