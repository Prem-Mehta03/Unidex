"""Turn a confirmed :class:`Interpretation` into stacks of files.

Past papers and teaching material are searched differently on purpose:

* **Papers and solutions** are described by their *metadata* (exam type, year), applied
  as filters. Topics are matched against the text inside the papers when that text has been
  read (``scripts/read_contents.py``). Papers whose text could not be read (scans,
  handwriting) cannot be checked, so they are listed after the matches and labelled. A
  solution is kept with a paper that matched. With no stored text, topics are ignored for
  papers and the student is told.
* **Notes, slides, tutorials, ...** are searched by topic using their *names* and, when
  available, the text inside them, with BM25 inside the chosen course. If nothing mentions
  the topics, all of that material is listed instead and the student is told.
"""

from dataclasses import dataclass, replace

from unidex.chat.models import (
    DEFAULT_MATERIALS,
    MATERIAL_DOC_TYPES,
    MATERIAL_LABELS,
    PAPER_MATERIALS,
    Interpretation,
    Material,
)
from unidex.search.catalog import Catalog, Hit, SearchFilters
from unidex.search.grouping import Stack, group_into_stacks
from unidex.search.labels import academic_year_label

# "quiz" and "test" are the same thing to a student, but folders use both words.
_RELATED_EXAMS = {"quiz": "test", "test": "quiz"}
_NUMBERED_EXAMS = frozenset({"quiz", "test", "lab_quiz"})


@dataclass(frozen=True, slots=True)
class PlanResult:
    """What a plan found.

    Attributes:
        stacks: Grouped results, best first.
        total_files: Number of files across all stacks.
        notes: Plain-language remarks about how the search was done.
        paper_years: Academic years that were used to filter papers, newest first.
    """

    stacks: list[Stack]
    total_files: int
    notes: tuple[str, ...]
    paper_years: tuple[int, ...] = ()


class ChatPlanner:
    """Runs the searches that answer a confirmed interpretation."""

    def __init__(self, catalog: Catalog) -> None:
        """Create a planner.

        Args:
            catalog: The searchable catalog.
        """
        self._catalog = catalog

    def run(self, interpretation: Interpretation) -> PlanResult:
        """Find the files that match an interpretation.

        Args:
            interpretation: The confirmed form.

        Returns:
            Stacks of files plus notes explaining how they were chosen.
        """
        materials = set(interpretation.materials or DEFAULT_MATERIALS)
        courses = frozenset(interpretation.courses)
        notes: list[str] = []
        hits: list[Hit] = []
        paper_years: tuple[int, ...] = ()

        paper_materials = materials & PAPER_MATERIALS
        if paper_materials:
            paper_hits, paper_years = self._papers(interpretation, courses, paper_materials, notes)
            hits.extend(paper_hits)
        other_materials = materials - PAPER_MATERIALS
        if other_materials:
            hits.extend(
                self._material(
                    interpretation, courses, other_materials, notes, apply_years=not paper_materials
                )
            )
        return PlanResult(
            stacks=group_into_stacks(hits),
            total_files=len(hits),
            notes=tuple(notes),
            paper_years=paper_years,
        )

    def _papers(
        self,
        interpretation: Interpretation,
        courses: frozenset[str],
        materials: set[Material],
        notes: list[str],
    ) -> tuple[list[Hit], tuple[int, ...]]:
        """Find papers and solutions using exam type and year filters."""
        doc_types = frozenset(t for m in materials for t in MATERIAL_DOC_TYPES[m])
        exams = set(interpretation.exam_types)
        exams |= {_RELATED_EXAMS[e] for e in exams if e in _RELATED_EXAMS}

        years = set(interpretation.years)
        if interpretation.recent_years:
            available = self._catalog.years_for(courses, doc_types, exams)
            years |= set(available[: interpretation.recent_years])
        filters = SearchFilters(
            courses=courses,
            doc_types=doc_types,
            exam_types=frozenset(exams),
            years=frozenset(years),
        )
        result = self._catalog.search("", filters, detect_courses=False)
        found = result.hits
        if interpretation.topics and self._catalog.has_content:
            found = self._by_content(" ".join(interpretation.topics), filters, found, notes)

        number = interpretation.quiz_number
        if number is not None and exams & _NUMBERED_EXAMS:
            found = [h for h in found if h.doc.exam_number == number]
            if not found:
                notes.append(f"No paper is labelled number {number}; try without the number.")
        if not found:
            available_years = self._catalog.years_for(courses, doc_types)
            if available_years:
                shown = ", ".join(academic_year_label(y) for y in available_years[:6])
                notes.append(f"No past papers match those filters. Years with papers: {shown}.")
            else:
                notes.append("No past papers are indexed for this course yet.")
        if interpretation.topics and not self._catalog.has_content:
            notes.append(
                "Past papers cannot be searched by topic yet (that needs reading the files), "
                "so every matching paper is shown."
            )
        return found, tuple(sorted(years, reverse=True))

    def _by_content(
        self, topic_text: str, filters: SearchFilters, candidates: list[Hit], notes: list[str]
    ) -> list[Hit]:
        """Keep papers that mention the topics, plus unreadable ones and matching solutions."""
        content = self._catalog.search_content(topic_text, filters)
        matched = list(content.matched)
        have = {h.doc.drive_file_id for h in matched}
        paired = _solutions_for(matched, candidates, have)
        unreadable = [
            h
            for h in content.unreadable
            if h.doc.drive_file_id not in have | {p.doc.drive_file_id for p in paired}
        ]
        if matched:
            notes.append("Papers were matched to your topics by reading the text inside them.")
        elif content.readable_total:
            notes.append(
                f"None of the {content.readable_total} readable papers mention your topics."
            )
        if unreadable:
            notes.append(
                f"{len(unreadable)} papers could not be read (scans or handwriting), so they "
                "are listed without a topic check."
            )
        return matched + paired + unreadable

    def _material(
        self,
        interpretation: Interpretation,
        courses: frozenset[str],
        materials: set[Material],
        notes: list[str],
        *,
        apply_years: bool,
    ) -> list[Hit]:
        """Find notes, slides and similar material; topics are matched against names."""
        doc_types = frozenset(t for m in materials for t in MATERIAL_DOC_TYPES[m])
        years = frozenset(interpretation.years) if apply_years else frozenset()
        filters = SearchFilters(courses=courses, doc_types=doc_types, years=years)
        topic_text = " ".join(interpretation.topics)
        found = self._catalog.search(topic_text, filters, detect_courses=False).hits
        inside = False
        if interpretation.topics and self._catalog.has_content:
            seen = {h.doc.drive_file_id for h in found}
            extra = [
                h
                for h in self._catalog.search_content(topic_text, filters).matched
                if h.doc.drive_file_id not in seen
            ]
            inside = bool(extra)
            found = found + extra
        if interpretation.topics:
            if found:
                where = (
                    "file and folder name and by the text inside them"
                    if inside
                    else ("file and folder name")
                )
                notes.append(f"Notes and slides were matched to your topics by {where}.")
            else:
                found = self._catalog.search("", filters, detect_courses=False).hits
                labels = ", ".join(MATERIAL_LABELS[m].lower() for m in Material if m in materials)
                notes.append(f"No {labels} file names mention your topics, so all of it is shown.")
        return self._drop_other_half(interpretation, found, notes)

    @staticmethod
    def _drop_other_half(
        interpretation: Interpretation, hits: list[Hit], notes: list[str]
    ) -> list[Hit]:
        """For a midsem-only target, hide slides that cover only the second half."""
        exams = set(interpretation.exam_types)
        if exams != {"midsem"}:
            return hits
        kept = [h for h in hits if h.doc.syllabus_scope != "post_midsem"]
        if len(kept) < len(hits):
            notes.append("Slides marked as post-midsem were hidden.")
        return kept


def _paper_key(hit: Hit) -> tuple[str, str, int | None, int | None, bool]:
    """Identify the exam a paper or solution belongs to."""
    doc = hit.doc
    return (doc.course_code, doc.exam_type, doc.academic_year, doc.exam_number, doc.is_makeup)


def _solutions_for(matched: list[Hit], candidates: list[Hit], have: set[str]) -> list[Hit]:
    """Return the solutions that belong to papers that matched (so they stay together)."""
    keys = {_paper_key(h) for h in matched if h.doc.doc_type != "solution"}
    return [
        replace(h, source="paired")
        for h in candidates
        if h.doc.doc_type == "solution"
        and h.doc.drive_file_id not in have
        and _paper_key(h) in keys
    ]
