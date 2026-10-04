"""JSON shapes returned by the API.

These pydantic models double as documentation: FastAPI turns them into the
interactive page at ``/docs`` and refuses to send anything that does not match.
"""

from pydantic import BaseModel


class FileCard(BaseModel):
    """One file inside a stack.

    Attributes:
        id: Drive file id (stable; used by the UI as a key).
        name: File name.
        folder: Folder path in the drive.
        url: Link that opens the original file in Drive.
        course_code: Course code, or empty.
        doc_type: Document type value, e.g. ``pyq``.
        doc_type_label: Document type as shown to students.
        exam_type: Exam type value, or empty when not applicable.
        exam_label: Exam type as shown, including quiz/test number, or empty.
        year: Start year of the academic year, if known.
        year_label: Academic year as shown, e.g. ``2023-24``, or empty.
        semester: Semester label, or empty.
        instructor: Instructor, or empty.
        is_makeup: Whether this is a make-up exam.
        has_solution: Whether solutions are included or this file is a solution.
        reviewed: True when a human checked the tags.
        uncertain: True when the tags came from guesswork and nobody has checked them.
        score: BM25 score (0 for filter-only searches).
        why: One sentence explaining the match.
    """

    id: str
    name: str
    folder: str
    url: str
    course_code: str
    doc_type: str
    doc_type_label: str
    exam_type: str
    exam_label: str
    year: int | None
    year_label: str
    semester: str
    instructor: str
    is_makeup: bool
    has_solution: bool
    reviewed: bool
    uncertain: bool
    score: float
    why: str


class StackOut(BaseModel):
    """A group of related files.

    Attributes:
        key: Stable identifier of the stack.
        title: Heading of the stack.
        course_code: Course code, or empty.
        kind: ``papers`` or a document type.
        file_count: Number of files in the stack.
        year_span: Years covered, or empty.
        solution_count: Files in the stack that are or include solutions.
        files: The files, in display order.
    """

    key: str
    title: str
    course_code: str
    kind: str
    file_count: int
    year_span: str
    solution_count: int
    files: list[FileCard]


class FacetOptionOut(BaseModel):
    """One choice in a filter group.

    Attributes:
        value: Value to send back as a filter.
        label: Text to show.
        count: Matching files if this option is added.
    """

    value: str
    label: str
    count: int


class DetectedCourse(BaseModel):
    """A course recognised in the query text and applied as a filter.

    Attributes:
        code: Course code, e.g. ``CS F213``.
        label: Text to show, e.g. ``CS F213 · Object Oriented Programming``.
    """

    code: str
    label: str


class SearchResponse(BaseModel):
    """Answer to a search.

    Attributes:
        query: The text that was searched.
        total_files: Files that matched.
        total_stacks: Stacks formed from the returned files.
        truncated: True when more files matched than were returned.
        stack_offset: Index of the first stack in ``stacks``.
        has_more: True when more stacks follow this page.
        stacks: This page of stacks.
        facets: Filter options with live counts, keyed by facet name.
        detected_courses: Courses recognised in the query text (for example "oop")
            and applied as a filter; the UI lets the student undo this.
    """

    query: str
    total_files: int
    total_stacks: int
    truncated: bool
    stack_offset: int
    has_more: bool
    stacks: list[StackOut]
    facets: dict[str, list[FacetOptionOut]]
    detected_courses: list[DetectedCourse]


class SuggestResponse(BaseModel):
    """Autocomplete answer.

    Attributes:
        suggestions: Words that complete the typed prefix.
    """

    suggestions: list[str]


class HealthResponse(BaseModel):
    """Liveness answer.

    Attributes:
        status: Always ``ok`` when the server is up.
        documents: Number of documents in the catalog.
    """

    status: str
    documents: int
