import sqlite3
from datetime import date

import pytest

from unidex.db.connection import transaction
from unidex.db.repositories import (
    CourseRepository,
    DepartmentRepository,
    RawFileRepository,
    SourceRepository,
)
from unidex.db.seed import seed_courses
from unidex.exceptions import IngestionError
from unidex.models.enums import DocType, ExamType, LinkStatus
from unidex.models.raw_file import RawFile

from ..conftest import ALIASES_CSV


def make_file(drive_id: str, name: str = "a.pdf", path: str = "/x") -> RawFile:
    return RawFile(
        drive_file_id=drive_id,
        path=path,
        name=name,
        extension="pdf",
        mime_type="application/pdf",
        modified_on=date(2025, 12, 4),
        url=f"https://drive.google.com/file/d/{drive_id}/view",
        is_indexable=True,
    )


def make_source(conn: sqlite3.Connection) -> int:
    department_id = DepartmentRepository(conn).get_or_create("CS")
    return SourceRepository(conn).get_or_create(department_id, "CS archive")


def test_get_or_create_is_idempotent(conn: sqlite3.Connection) -> None:
    departments = DepartmentRepository(conn)
    assert departments.get_or_create("CS") == departments.get_or_create("CS")
    sources = SourceRepository(conn)
    department_id = departments.get_or_create("CS")
    assert sources.get_or_create(department_id, "A") == sources.get_or_create(department_id, "A")


def test_foreign_keys_are_enforced(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        RawFileRepository(conn).upsert_many(999, [make_file("f1")], "2026-10-04T00:00:00+00:00")


def test_upsert_many_inserts_then_updates(conn: sqlite3.Connection) -> None:
    source_id = make_source(conn)
    repo = RawFileRepository(conn)
    first = repo.upsert_many(source_id, [make_file("f1"), make_file("f2")], "t1")
    assert (first.inserted, first.updated) == (2, 0)

    moved = make_file("f1", name="renamed.pdf", path="/elsewhere")
    second = repo.upsert_many(source_id, [moved, make_file("f3")], "t2")
    assert (second.inserted, second.updated) == (1, 1)

    assert repo.count() == 3
    stored = {f.drive_file_id: f for f in repo.iter_all()}
    assert stored["f1"].name == "renamed.pdf"
    assert stored["f1"].path == "/elsewhere"
    assert stored["f1"].modified_on == date(2025, 12, 4)


def test_first_seen_is_kept_and_last_seen_moves(conn: sqlite3.Connection) -> None:
    source_id = make_source(conn)
    repo = RawFileRepository(conn)
    repo.upsert_many(source_id, [make_file("f1")], "t1")
    repo.upsert_many(source_id, [make_file("f1")], "t2")
    row = conn.execute("SELECT first_seen_at, last_seen_at FROM raw_files").fetchone()
    assert (row["first_seen_at"], row["last_seen_at"]) == ("t1", "t2")


def test_count_indexable_only(conn: sqlite3.Connection) -> None:
    source_id = make_source(conn)
    code = RawFile("f9", "/x", "a.v", "v", "x", None, "u", False)
    repo = RawFileRepository(conn)
    repo.upsert_many(source_id, [make_file("f1"), code], "t1")
    assert repo.count() == 2
    assert repo.count(indexable_only=True) == 1


def test_transaction_rolls_back_on_error(conn: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), transaction(conn):
        DepartmentRepository(conn).get_or_create("CS")
        raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) AS n FROM departments").fetchone()["n"] == 0


def test_transaction_commits_on_success(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        DepartmentRepository(conn).get_or_create("CS")
    assert conn.execute("SELECT COUNT(*) AS n FROM departments").fetchone()["n"] == 1


def test_seed_courses_and_alias_resolution(conn: sqlite3.Connection) -> None:
    seed_courses(conn, ALIASES_CSV)
    courses = CourseRepository(conn)
    oop = courses.resolve("OOP")
    assert oop is not None
    assert courses.resolve("cs f213") == oop
    assert courses.resolve("CSF213") == oop
    assert courses.resolve("Object Oriented Programming") == oop
    assert courses.resolve("M3") != oop
    assert courses.resolve("not a course") is None


def test_seed_is_repeatable(conn: sqlite3.Connection) -> None:
    seed_courses(conn, ALIASES_CSV)
    before = conn.execute("SELECT COUNT(*) AS n FROM course_aliases").fetchone()["n"]
    seed_courses(conn, ALIASES_CSV)
    after = conn.execute("SELECT COUNT(*) AS n FROM course_aliases").fetchone()["n"]
    assert before == after


def test_alias_cannot_point_at_two_courses(conn: sqlite3.Connection) -> None:
    department_id = DepartmentRepository(conn).get_or_create("CS")
    courses = CourseRepository(conn)
    first = courses.upsert("CS F1", "One", department_id)
    second = courses.upsert("CS F2", "Two", department_id)
    courses.add_alias("shared", first)
    with pytest.raises(IngestionError):
        courses.add_alias("Shared", second)


def test_enum_values_match_database_constraints(conn: sqlite3.Connection) -> None:
    """Every Python enum value must be accepted by the table's CHECK constraint."""
    source_id = make_source(conn)
    RawFileRepository(conn).upsert_many(source_id, [make_file("f1")], "t")
    raw_id = conn.execute("SELECT id FROM raw_files").fetchone()["id"]
    sql = (
        "INSERT INTO documents (raw_file_id, title, drive_url, mime_type, exam_type, doc_type,"
        " link_status, created_at) VALUES (?, 't', 'u', 'm', ?, ?, ?, 'now')"
    )
    for exam_type in ExamType:
        conn.execute(sql, (raw_id, exam_type.value, "unknown", "unknown"))
        conn.execute("DELETE FROM documents")
    for doc_type in DocType:
        conn.execute(sql, (raw_id, "unknown", doc_type.value, "unknown"))
        conn.execute("DELETE FROM documents")
    for link_status in LinkStatus:
        conn.execute(sql, (raw_id, "unknown", "unknown", link_status.value))
        conn.execute("DELETE FROM documents")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, (raw_id, "not-an-exam", "unknown", "unknown"))
