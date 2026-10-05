"""Convert catalog objects into the API's JSON models."""

from unidex.api.schemas import FacetOptionOut, FileCard, StackOut
from unidex.models.metadata import REVIEW_THRESHOLD
from unidex.search.catalog import FacetOption, Hit
from unidex.search.grouping import Stack
from unidex.search.labels import DOC_TYPE_LABELS, EXAM_TYPE_LABELS, academic_year_label

MANUAL = "manual"
_SAFE_URL_PREFIXES = ("https://", "http://")
_NUMBERED_EXAMS = frozenset({"quiz", "test", "lab_quiz"})


def exam_label(exam_type: str, exam_number: int | None, is_makeup: bool) -> str:
    """Describe the exam a file belongs to, e.g. ``"Quiz 2 (make-up)"``.

    Args:
        exam_type: Exam type value.
        exam_number: Quiz or test number, if any.
        is_makeup: Whether it is a make-up exam.

    Returns:
        The label, or an empty string when the file has no exam.
    """
    base = EXAM_TYPE_LABELS.get(exam_type, "")
    if not base:
        return ""
    if exam_number is not None and exam_type in _NUMBERED_EXAMS:
        base = f"{base} {exam_number}"
    return f"{base} (make-up)" if is_makeup else base


def safe_url(url: str) -> str:
    """Return the link only if it is a plain web address.

    A link such as ``javascript:...`` would run code when clicked, so anything
    that is not http(s) is replaced by an empty string.

    Args:
        url: A stored link.

    Returns:
        The link, or an empty string.
    """
    return url if url.lower().startswith(_SAFE_URL_PREFIXES) else ""


def why_matched(hit: Hit) -> str:
    """Explain a match in one sentence a student can check.

    Args:
        hit: The search hit.

    Returns:
        A sentence naming the matched words, or saying the filters matched.
    """
    if hit.source == "unreadable":
        return (
            "The text inside this file could not be read (scan or handwriting), so it was "
            "not checked for your topics. Shown because its course and exam match."
        )
    if hit.source == "paired":
        return "The solution for a paper that mentions your topics."
    if hit.source == "content" and hit.matched_terms:
        words = ", ".join(f"“{word}”" for word in hit.matched_terms)
        return f"Mentions {words} inside the file."
    if hit.matched_terms:
        words = ", ".join(f"“{word}”" for word in hit.matched_terms)
        return f"Matches {words} in the file name or folder."
    return "Matches your filters."


def to_card(hit: Hit) -> FileCard:
    """Convert a hit into a file card.

    Args:
        hit: The search hit.

    Returns:
        The JSON model.
    """
    doc = hit.doc
    reviewed = doc.method == MANUAL
    return FileCard(
        id=doc.drive_file_id,
        name=doc.name,
        folder=doc.path,
        url=safe_url(doc.url),
        course_code=doc.course_code,
        doc_type=doc.doc_type,
        doc_type_label=DOC_TYPE_LABELS.get(doc.doc_type, doc.doc_type),
        exam_type=doc.exam_type if doc.exam_type in EXAM_TYPE_LABELS else "",
        exam_label=exam_label(doc.exam_type, doc.exam_number, doc.is_makeup),
        year=doc.academic_year,
        year_label=academic_year_label(doc.academic_year) if doc.academic_year else "",
        semester=f"Sem {doc.semester}" if doc.semester else "",
        instructor=doc.instructor,
        is_makeup=doc.is_makeup,
        has_solution=doc.has_solution or doc.doc_type == "solution",
        reviewed=reviewed,
        uncertain=not reviewed and doc.confidence < REVIEW_THRESHOLD,
        score=round(hit.score, 3),
        why=why_matched(hit),
    )


def to_stack(stack: Stack) -> StackOut:
    """Convert a stack into its JSON model.

    Args:
        stack: The stack.

    Returns:
        The JSON model.
    """
    return StackOut(
        key=stack.key,
        title=stack.title,
        course_code=stack.course_code,
        kind=stack.kind,
        file_count=len(stack.hits),
        year_span=stack.year_span,
        solution_count=stack.solution_count,
        files=[to_card(hit) for hit in stack.hits],
    )


def to_facet_options(options: list[FacetOption]) -> list[FacetOptionOut]:
    """Convert facet options into their JSON models.

    Args:
        options: Options from the catalog.

    Returns:
        The JSON models.
    """
    return [FacetOptionOut(value=o.value, label=o.label, count=o.count) for o in options]
