import pytest

from unidex.extraction.path_parser import clean_instructor, parse_path, parse_semester_folder
from unidex.normalize import normalize_alias

COURSES = {"oop": 1, "lcs": 2}


def resolve(text: str) -> int | None:
    return COURSES.get(normalize_alias(text))


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("Sem 1 25-26 (Anupama Sharma)", (1, 2025, "Anupama Sharma")),
        ("25-26 Sem 1 (Neena Goveas)", (1, 2025, "Neena Goveas")),
        ("21-22 Sem 1(CS_24 - Anup Mathew)", (1, 2021, "Anup Mathew")),
        ("Sem 1 18-19", (1, 2018, None)),
        ("19-20 Sem 1", (1, 2019, None)),
        ("Sem 2 24-25 (X)", (2, 2024, "X")),
    ],
)
def test_both_semester_spellings_are_read(
    folder: str, expected: tuple[int, int, str | None]
) -> None:
    assert parse_semester_folder(folder) == expected


@pytest.mark.parametrize("folder", ["Slides", "Sem 1 25-27 (X)", "Sem 1", "2025", "Sem 1 99-00"])
def test_other_folders_are_not_semester_folders(folder: str) -> None:
    assert parse_semester_folder(folder) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("CS_24 - Anup Mathew", "Anup Mathew"),
        ("CS_25 - RPJ", "RPJ"),
        ("Baskar, Ashwin", "Baskar, Ashwin"),
        ("", None),
        (None, None),
        ("CS_24 - ", None),
    ],
)
def test_clean_instructor(raw: str | None, expected: str | None) -> None:
    assert clean_instructor(raw) == expected


def test_group_prefix_is_skipped_when_finding_the_course() -> None:
    parsed = parse_path("/2-1 CDCs/OOP/Sem 1 25-26 (X)/Quizzes", resolve)
    assert parsed.course_id == 1
    assert parsed.semester == 1
    assert parsed.folder_year == 2025
    assert parsed.category_folders == ("Quizzes",)


def test_course_can_be_the_first_folder() -> None:
    parsed = parse_path("/LCS/Sem 1 18-19", resolve)
    assert parsed.course_id == 2
    assert parsed.folder_year == 2018
    assert parsed.category_folders == ()


def test_path_without_a_semester_folder_keeps_every_folder_below_the_course() -> None:
    parsed = parse_path("/OOP/Misc/Extra", resolve)
    assert parsed.folder_year is None
    assert parsed.category_folders == ("Misc", "Extra")


def test_unknown_course_is_reported_as_none() -> None:
    parsed = parse_path("/Biology/Sem 1 25-26 (X)", resolve)
    assert parsed.course_id is None
    assert parsed.folder_year == 2025
