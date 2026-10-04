import csv
import sqlite3
from pathlib import Path

import pytest

from unidex.db.repositories import ReviewQueueRepository
from unidex.db.seed import seed_courses
from unidex.exceptions import ExtractionError
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.extraction.review_io import EXPORT_COLUMNS, export_review, import_review
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.sync_job import SyncJob

from ..conftest import ALIASES_CSV, FIXED_NOW, fixed_clock


@pytest.fixture
def ready(conn: sqlite3.Connection, tmp_path: Path) -> sqlite3.Connection:
    """A database whose review queue has at least a few rows."""
    seed_courses(conn, ALIASES_CSV)
    listing = tmp_path / "drive.csv"
    listing.write_text(
        "path,name,mime_type,modified,url\n"
        "/OOP/Sem 1 25-26 (X),Mystery.pdf,application/pdf,10/1/2025,https://drive.google.com/file/d/a1/view\n"
        "/OOP/Sem 1 25-26 (X),Other thing.pdf,application/pdf,10/1/2025,https://drive.google.com/file/d/a2/view\n"
        "/OOP/Sem 1 25-26 (X)/Quizzes,Quiz 1.pdf,application/pdf,10/1/2025,https://drive.google.com/file/d/a3/view\n",
        encoding="utf-8",
    )
    SyncJob(conn, CsvFileSource(listing), "CS", "CS archive", clock=fixed_clock).run()
    ExtractionPipeline(conn, clock=fixed_clock).run()
    return conn


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_export_lists_only_open_items_with_a_reason(
    ready: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "review.csv"
    assert export_review(ready, out) == 2
    rows = read_rows(out)
    assert {row["name"] for row in rows} == {"Mystery.pdf", "Other thing.pdf"}
    assert all(row["reason"] for row in rows)
    assert set(rows[0]) == set(EXPORT_COLUMNS)


def test_import_confirms_rows_and_closes_the_queue_items(
    ready: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "review.csv"
    export_review(ready, out)
    rows = read_rows(out)
    for row in rows:
        row["doc_type"], row["exam_type"], row["academic_year"] = "notes", "none", "2025"
    write_rows(out, rows)
    assert import_review(ready, out, clock=lambda: FIXED_NOW) == 2
    assert ReviewQueueRepository(ready).count_open() == 0
    stored = ready.execute(
        "SELECT doc_type, extraction_method, reviewed FROM documents WHERE doc_type = 'notes'"
    ).fetchall()
    assert len(stored) == 2
    assert all(r["extraction_method"] == "manual" and r["reviewed"] == 1 for r in stored)


def test_rows_deleted_from_the_file_stay_in_the_queue(
    ready: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "review.csv"
    export_review(ready, out)
    rows = read_rows(out)
    keep = rows[0]
    keep["doc_type"], keep["exam_type"] = "notes", "none"
    write_rows(out, [keep])
    import_review(ready, out, clock=lambda: FIXED_NOW)
    assert ReviewQueueRepository(ready).count_open() == 1


def test_invalid_rows_reject_the_whole_file_and_change_nothing(
    ready: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "review.csv"
    export_review(ready, out)
    rows = read_rows(out)
    rows[0]["doc_type"], rows[0]["exam_type"] = "notes", "none"  # valid
    rows[1]["doc_type"], rows[1]["exam_type"] = "notes", "none"
    rows[1]["academic_year"] = "1850"  # invalid
    write_rows(out, rows)
    with pytest.raises(ExtractionError, match="1 invalid row"):
        import_review(ready, out, clock=lambda: FIXED_NOW)
    assert ReviewQueueRepository(ready).count_open() == 2
    assert (
        ready.execute("SELECT COUNT(*) AS n FROM documents WHERE reviewed = 1").fetchone()["n"] == 0
    )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("doc_type", "unknown"),
        ("doc_type", "banana"),
        ("exam_type", "banana"),
        ("exam_number", "x"),
        ("exam_number", "500"),
        ("is_makeup", "maybe"),
        ("syllabus_scope", "after_lunch"),
    ],
)
def test_each_bad_cell_is_reported(
    ready: sqlite3.Connection, tmp_path: Path, column: str, value: str
) -> None:
    out = tmp_path / "review.csv"
    export_review(ready, out)
    rows = read_rows(out)
    rows[0].update(doc_type="pyq", exam_type="quiz")
    rows[0][column] = value
    write_rows(out, rows[:1])
    with pytest.raises(ExtractionError):
        import_review(ready, out, clock=lambda: FIXED_NOW)


def test_exam_type_may_not_stay_unknown_for_papers(
    ready: sqlite3.Connection, tmp_path: Path
) -> None:
    out = tmp_path / "review.csv"
    export_review(ready, out)
    rows = read_rows(out)
    rows[0].update(doc_type="pyq", exam_type="unknown")
    write_rows(out, rows[:1])
    with pytest.raises(ExtractionError, match="exam_type is still"):
        import_review(ready, out, clock=lambda: FIXED_NOW)


def test_file_with_missing_columns_is_rejected(ready: sqlite3.Connection, tmp_path: Path) -> None:
    broken = tmp_path / "broken.csv"
    broken.write_text("drive_file_id,doc_type\nx,notes\n", encoding="utf-8")
    with pytest.raises(ExtractionError, match="missing columns"):
        import_review(ready, broken)


def test_missing_file_is_a_clear_error(ready: sqlite3.Connection, tmp_path: Path) -> None:
    with pytest.raises(ExtractionError, match="Cannot read"):
        import_review(ready, tmp_path / "nope.csv")
