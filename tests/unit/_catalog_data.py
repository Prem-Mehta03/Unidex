"""Shared fixtures: a small catalog of invented documents."""

from unidex.db.repositories import DocumentView


def make_view(
    name: str,
    *,
    path: str = "/OOP/Sem 1 23-24 (X)",
    doc_type: str = "pyq",
    exam_type: str = "midsem",
    year: int | None = 2023,
    course_code: str = "CS F213",
    course_name: str = "Object Oriented Programming",
    has_solution: bool = False,
    confidence: float = 0.9,
    method: str = "rule",
    url: str | None = None,
    exam_number: int | None = None,
) -> DocumentView:
    """Build a document view with sensible defaults."""
    return DocumentView(
        drive_file_id=f"id-{name}",
        path=path,
        name=name,
        url=url if url is not None else f"https://drive.google.com/file/d/{name}",
        doc_type=doc_type,
        exam_type=exam_type,
        exam_number=exam_number,
        academic_year=year,
        is_makeup=False,
        has_solution=has_solution,
        confidence=confidence,
        method=method,
        course_code=course_code,
        course_name=course_name,
    )


def sample_views() -> list[DocumentView]:
    """Return ten documents across two courses."""
    return [
        make_view("OOP Midsem 2023.pdf"),
        make_view("OOP Midsem 2022.pdf", year=2022),
        make_view("OOP Midsem 2023 Solutions.pdf", doc_type="solution", has_solution=True),
        make_view("OOP Compre 2023.pdf", exam_type="compre"),
        make_view("Inheritance Slides.pdf", doc_type="slides", exam_type="none", year=2023),
        make_view(
            "Laplace Transform Notes.pdf",
            path="/M3/Sem 1 25-26 (Y)",
            doc_type="notes",
            exam_type="none",
            year=2025,
            course_code="MATH F211",
            course_name="Mathematics III",
        ),
        make_view(
            "M3 Midsem 2025.pdf",
            path="/M3/Sem 1 25-26 (Y)",
            year=2025,
            course_code="MATH F211",
            course_name="Mathematics III",
        ),
        make_view(
            "Midsem Guess Paper.pdf",
            path="/M3/Sem 1 25-26 (Y)",
            year=2025,
            course_code="MATH F211",
            course_name="Mathematics III",
            confidence=0.55,
        ),
        make_view("Verilog Installation Guide.pdf", doc_type="other", exam_type="none", year=None),
        make_view(
            "Checked Quiz 2.pdf",
            exam_type="quiz",
            exam_number=2,
            confidence=1.0,
            method="manual",
        ),
    ]
