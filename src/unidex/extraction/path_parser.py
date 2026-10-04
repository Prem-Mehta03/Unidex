"""Split a drive folder path into the parts that carry meaning.

The CS drive follows a loose convention::

    /<optional group>/<course nickname>/<semester folder>/<category>/...

for example ``/2-1 CDCs/M3/Sem 1 25-26 (Anupama Sharma)/Quizzes``. The semester
folder comes in two spellings (``Sem 1 25-26 (X)`` and ``25-26 Sem 1 (X)``) and
the instructor part is optional. This module reads that structure; it does not
decide what a file *is* (that is :mod:`unidex.extraction.rules`).
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

_SEM_FIRST = re.compile(
    r"^sem\s*(?P<sem>\d)\s+(?P<y1>\d{2})\s*-\s*(?P<y2>\d{2})\s*(?:\((?P<who>[^)]*)\))?\s*$",
    re.IGNORECASE,
)
_YEAR_FIRST = re.compile(
    r"^(?P<y1>\d{2})\s*-\s*(?P<y2>\d{2})\s+sem\s*(?P<sem>\d)\s*(?:\((?P<who>[^)]*)\))?\s*$",
    re.IGNORECASE,
)
# Folder owners prefix the instructor with a batch tag such as "CS_24 - ".
_BATCH_TAG = re.compile(r"^\s*CS_\d+\s*-\s*", re.IGNORECASE)

MIN_PLAUSIBLE_YEAR_PREFIX = 10  # "10-11" is the earliest academic year we accept
MAX_PLAUSIBLE_YEAR_PREFIX = 40


@dataclass(frozen=True, slots=True)
class ParsedPath:
    """The meaningful parts of a folder path.

    Attributes:
        course_id: Course the path belongs to, or ``None`` if no folder matched.
        semester: Semester number from the semester folder, if any.
        folder_year: Start year of the academic year in the semester folder.
        instructor: Instructor text from the semester folder, if any.
        category_folders: Folders below the semester folder (or below the
            course folder when there is no semester folder), outermost first.
    """

    course_id: int | None
    semester: int | None
    folder_year: int | None
    instructor: str | None
    category_folders: tuple[str, ...]


def clean_instructor(text: str | None) -> str | None:
    """Tidy the text inside the parentheses of a semester folder.

    Args:
        text: Raw text such as ``"CS_24 - Anup Mathew"`` or ``"Baskar, Ashwin"``.

    Returns:
        The instructor text without the batch tag, or ``None`` if nothing is left.
    """
    if text is None:
        return None
    cleaned = _BATCH_TAG.sub("", text).strip()
    return cleaned or None


def parse_semester_folder(folder: str) -> tuple[int, int, str | None] | None:
    """Read a semester folder name in either spelling.

    Args:
        folder: One path component, e.g. ``"Sem 1 25-26 (Baskar)"``.

    Returns:
        ``(semester, start_year, instructor)`` or ``None`` if the name is not a
        semester folder, or its two years are not consecutive (``25-27``).
    """
    match = _SEM_FIRST.match(folder.strip()) or _YEAR_FIRST.match(folder.strip())
    if match is None:
        return None
    start, end = int(match["y1"]), int(match["y2"])
    if not MIN_PLAUSIBLE_YEAR_PREFIX <= start <= MAX_PLAUSIBLE_YEAR_PREFIX:
        return None
    if (start + 1) % 100 != end:
        return None
    return int(match["sem"]), 2000 + start, clean_instructor(match["who"])


def parse_path(path: str, resolve_course: Callable[[str], int | None]) -> ParsedPath:
    """Break a folder path into course, semester and category folders.

    The course folder is the first or second component: some exports start
    with a group folder (``2-1 CDCs``) and some do not.

    Args:
        path: Folder path of a file, e.g. ``/OOP/23-24 Sem 1 (R. Joshi)/Midsem``.
        resolve_course: Maps a folder name to a course id, or ``None``.

    Returns:
        The parsed structure; fields are ``None``/empty when not present.
    """
    parts = [part.strip() for part in path.split("/") if part.strip()]
    course_id: int | None = None
    course_index = -1
    for index, part in enumerate(parts[:2]):
        found = resolve_course(part)
        if found is not None:
            course_id, course_index = found, index
            break

    below_course = parts[course_index + 1 :] if course_index >= 0 else parts
    for index, part in enumerate(below_course):
        parsed = parse_semester_folder(part)
        if parsed is not None:
            semester, year, instructor = parsed
            return ParsedPath(
                course_id, semester, year, instructor, tuple(below_course[index + 1 :])
            )
    return ParsedPath(course_id, None, None, None, tuple(below_course))
