"""Human-readable names for the controlled vocabularies.

The database stores machine values such as ``lab_compre``; students should see
"Lab compre". Keeping the wording in one place means the API and the stack
titles never disagree.
"""

DOC_TYPE_LABELS: dict[str, str] = {
    "pyq": "Past papers",
    "solution": "Solutions",
    "notes": "Notes",
    "slides": "Lecture slides",
    "tutorial": "Tutorials",
    "assignment": "Assignments",
    "lab": "Lab material",
    "cheatsheet": "Cheat sheets",
    "handout": "Handouts",
    "textbook": "Textbooks",
    "grade_stats": "Grade statistics",
    "other": "Other files",
    "unknown": "Unsorted files",
}

EXAM_TYPE_LABELS: dict[str, str] = {
    "quiz": "Quiz",
    "test": "Test",
    "midsem": "Midsem",
    "compre": "Compre",
    "lab_compre": "Lab compre",
    "lab_quiz": "Lab quiz",
}

# Exam types that are real evaluations (``none`` and ``unknown`` are not).
EXAM_TYPES_SHOWN = tuple(EXAM_TYPE_LABELS)


def academic_year_label(year: int) -> str:
    """Format the start year of an academic year, e.g. ``2023`` -> ``2023-24``.

    Args:
        year: First calendar year of the academic year.

    Returns:
        The label shown to students.
    """
    return f"{year}-{(year + 1) % 100:02d}"
