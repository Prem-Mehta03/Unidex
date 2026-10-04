"""The result of interpreting one file: what it is, which course, which exam."""

from dataclasses import dataclass, field

from unidex.models.enums import DocType, ExamType, ExtractionMethod, SyllabusScope

# Results below this confidence go to the review queue.
REVIEW_THRESHOLD = 0.7


@dataclass(frozen=True, slots=True)
class ExtractedMetadata:
    """Everything the extractor concluded about a single file.

    Attributes:
        course_id: Database id of the course, or ``None`` if no alias matched.
        academic_year: Year the academic year *started*, e.g. 2025 for ``25-26``.
            ``None`` when it cannot be trusted (for example a paper found in a
            "Past Material" folder that gives no year in its name).
        semester: Semester number (1 or 2), if the folder says so.
        instructor: Instructor text from the semester folder, if present.
        exam_type: Which evaluation the file belongs to (``NONE`` if not applicable).
        exam_number: Quiz or test number, e.g. 2 for "Quiz 2".
        doc_type: What kind of material the file is.
        is_makeup: Whether the file is for a make-up exam.
        has_solution: Whether the file contains worked solutions or an answer key.
        syllabus_scope: ``PRE_MIDSEM`` / ``POST_MIDSEM`` for lecture files filed
            under a folder like ``Danumjaya (Pre Midsem)``; otherwise ``None``.
        title: Display title derived from the file name.
        confidence: Overall confidence from 0 to 1 (the weakest field decides).
        method: How the values were produced.
        notes: Short human-readable reasons, shown in the review queue.
    """

    course_id: int | None
    academic_year: int | None
    semester: int | None
    instructor: str | None
    exam_type: ExamType
    exam_number: int | None
    doc_type: DocType
    is_makeup: bool
    has_solution: bool
    title: str
    confidence: float
    method: ExtractionMethod = ExtractionMethod.RULE
    syllabus_scope: SyllabusScope | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_llm(self) -> bool:
        """Whether the rules left something a language model could still decide."""
        if self.doc_type is DocType.UNKNOWN:
            return True
        return self.doc_type in (DocType.PYQ, DocType.SOLUTION) and self.exam_type is (
            ExamType.UNKNOWN
        )

    @property
    def review_reasons(self) -> tuple[str, ...]:
        """Reasons a human should look at this file (empty if it looks fine)."""
        reasons: list[str] = []
        if self.course_id is None:
            reasons.append("course not recognised")
        if self.doc_type is DocType.UNKNOWN:
            reasons.append("document type unknown")
        if self.doc_type in (DocType.PYQ, DocType.SOLUTION) and self.exam_type is ExamType.UNKNOWN:
            reasons.append("exam type unknown")
        if self.doc_type in (DocType.PYQ, DocType.SOLUTION) and self.academic_year is None:
            reasons.append("academic year unknown")
        if self.method is ExtractionMethod.LLM:
            reasons.append("suggested by the language model; please confirm")
        if self.confidence < REVIEW_THRESHOLD:
            reasons.extend(note for note in self.notes if note not in reasons)
        return tuple(reasons)
