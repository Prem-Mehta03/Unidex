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
    syllabus_scope: str = "",
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
        syllabus_scope=syllabus_scope,
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


ALIASES = {"oop": "CS F213", "dd": "CS F215", "m3": "MATH F211"}


def chat_views() -> list[DocumentView]:
    """Return documents shaped like a real drive: papers over years, slides, notes."""
    oop = {"course_code": "CS F213", "course_name": "Object Oriented Programming"}
    dd = {"course_code": "CS F215", "course_name": "Digital Design"}
    views = [
        make_view(f"OOP Midsem {y}.pdf", year=y, exam_type="midsem", **oop)
        for y in (2021, 2022, 2023, 2024)
    ]
    views += [
        make_view(f"OOP Compre {y}.pdf", year=y, exam_type="compre", **oop) for y in (2022, 2023)
    ]
    views += [
        make_view("OOP Midsem 2022 Solutions.pdf", year=2022, doc_type="solution", **oop),
        make_view("OOP Quiz 1.pdf", year=2023, exam_type="test", exam_number=1, **oop),
        make_view("OOP Quiz 2.pdf", year=2023, exam_type="quiz", exam_number=2, **oop),
        make_view(
            "Inheritance Lecture 3.pdf", doc_type="slides", exam_type="none", year=2023, **oop
        ),
        make_view(
            "Polymorphism Lecture 4.pdf",
            doc_type="slides",
            exam_type="none",
            year=2023,
            syllabus_scope="pre_midsem",
            **oop,
        ),
        make_view(
            "Generics Lecture 9.pdf",
            doc_type="slides",
            exam_type="none",
            year=2023,
            syllabus_scope="post_midsem",
            **oop,
        ),
        make_view("Java Cheatsheet.pdf", doc_type="cheatsheet", exam_type="none", year=2023, **oop),
        make_view("DD Compre 2024.pdf", year=2024, exam_type="compre", **dd),
        make_view("DD Compre 2025.pdf", year=2025, exam_type="compre", **dd),
        make_view("DD Midsem 2025.pdf", year=2025, exam_type="midsem", **dd),
        make_view("Karnaugh Maps Notes.pdf", doc_type="notes", exam_type="none", year=2025, **dd),
    ]
    return views
