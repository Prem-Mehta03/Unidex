"""Export the review queue to a CSV, and import the corrected CSV back.

Workflow for the person reviewing:

1. ``python scripts/review_queue.py export`` writes ``review.csv``.
2. Open it in a spreadsheet. Fix any wrong value in the editable columns and
   delete rows you do not want to confirm yet.
3. ``python scripts/review_queue.py import`` stores every row that is still in
   the file as human-confirmed. Confirmed rows are never overwritten by a later
   automatic run.
"""

import csv
import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from unidex.db.connection import transaction
from unidex.db.repositories import (
    DocumentRepository,
    ReviewItem,
    ReviewQueueRepository,
)
from unidex.exceptions import ExtractionError
from unidex.extraction.rules import EXAM_RELEVANT
from unidex.models.enums import DocType, ExamType, SyllabusScope

logger = logging.getLogger(__name__)

EXPORT_COLUMNS = (
    "drive_file_id",
    "reason",
    "path",
    "name",
    "url",
    "doc_type",
    "exam_type",
    "exam_number",
    "academic_year",
    "is_makeup",
    "has_solution",
    "syllabus_scope",
)
MAX_REPORTED_ERRORS = 10
MIN_YEAR = 2000
MAX_YEAR = 2100
MAX_EXAM_NUMBER = 20
_TRUE = frozenset({"1", "true", "yes", "y"})
_FALSE = frozenset({"0", "false", "no", "n", ""})


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def export_review(conn: sqlite3.Connection, path: Path) -> int:
    """Write the open review items to a CSV file.

    Args:
        conn: An open connection.
        path: Where to write the CSV (overwritten if present).

    Returns:
        The number of rows written.
    """
    items = list(ReviewQueueRepository(conn).iter_open())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_COLUMNS)
        writer.writeheader()
        for item in items:
            writer.writerow(_item_to_row(item))
    logger.info("Wrote %d review rows to %s", len(items), path)
    return len(items)


def _item_to_row(item: ReviewItem) -> dict[str, str]:
    """Convert a review item to CSV cell text."""
    return {
        "drive_file_id": item.drive_file_id,
        "reason": item.reason,
        "path": item.path,
        "name": item.name,
        "url": item.url,
        "doc_type": item.doc_type,
        "exam_type": item.exam_type,
        "exam_number": "" if item.exam_number is None else str(item.exam_number),
        "academic_year": "" if item.academic_year is None else str(item.academic_year),
        "is_makeup": "yes" if item.is_makeup else "no",
        "has_solution": "yes" if item.has_solution else "no",
        "syllabus_scope": item.syllabus_scope,
    }


def _parse_bool(text: str) -> bool:
    """Read yes/no style cell text; raises ValueError for anything else."""
    lowered = text.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ValueError(f"expected yes or no, got {text!r}")


def _parse_optional_int(text: str, low: int, high: int, label: str) -> int | None:
    """Read an optional whole number within a range; raises ValueError otherwise."""
    cleaned = text.strip()
    if not cleaned:
        return None
    value = int(cleaned)
    if not low <= value <= high:
        raise ValueError(f"{label} must be between {low} and {high}, got {value}")
    return value


def _parse_row(row: dict[str, str]) -> dict[str, str | int | None]:
    """Validate one CSV row and convert it to database column values.

    Raises:
        ValueError: With a readable message if any cell is invalid.
    """
    doc_type = DocType(row["doc_type"].strip().lower())
    exam_type = ExamType(row["exam_type"].strip().lower())
    if doc_type is DocType.UNKNOWN:
        raise ValueError("doc_type is still 'unknown'; choose a real value or delete the row")
    if exam_type is ExamType.UNKNOWN and doc_type in EXAM_RELEVANT:
        raise ValueError("exam_type is still 'unknown'; choose a real value or delete the row")
    scope_text = row["syllabus_scope"].strip().lower()
    return {
        "doc_type": doc_type.value,
        "exam_type": exam_type.value,
        "exam_number": _parse_optional_int(row["exam_number"], 1, MAX_EXAM_NUMBER, "exam_number"),
        "academic_year": _parse_optional_int(row["academic_year"], MIN_YEAR, MAX_YEAR, "year"),
        "is_makeup": int(_parse_bool(row["is_makeup"])),
        "has_solution": int(_parse_bool(row["has_solution"])),
        "syllabus_scope": SyllabusScope(scope_text).value if scope_text else None,
    }


def import_review(
    conn: sqlite3.Connection,
    path: Path,
    clock: Callable[[], datetime] = _utc_now,
) -> int:
    """Store human-corrected rows and close their review items.

    Validation covers the whole file before anything is written, and the
    writes happen in one transaction: either every row is applied or none.

    Args:
        conn: An open connection.
        path: The CSV produced by :func:`export_review` and then edited.
        clock: Returns the current time; replaced in tests.

    Returns:
        The number of documents updated.

    Raises:
        ExtractionError: If the file is unreadable, lacks required columns, or
            any row has an invalid value (the message lists the first problems).
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = set(EXPORT_COLUMNS) - set(reader.fieldnames or ())
            if missing:
                raise ExtractionError(f"Review file is missing columns: {sorted(missing)}")
            rows = list(reader)
    except OSError as exc:
        raise ExtractionError(f"Cannot read review file {path}: {exc}") from exc

    parsed: list[tuple[str, dict[str, str | int | None]]] = []
    errors: list[str] = []
    for line, row in enumerate(rows, start=2):
        try:
            parsed.append((row["drive_file_id"].strip(), _parse_row(row)))
        except ValueError as exc:
            errors.append(f"line {line} ({row.get('name', '?')}): {exc}")
    if errors:
        shown = "\n  ".join(errors[:MAX_REPORTED_ERRORS])
        more = len(errors) - MAX_REPORTED_ERRORS
        suffix = f"\n  ... and {more} more" if more > 0 else ""
        raise ExtractionError(f"Review file has {len(errors)} invalid row(s):\n  {shown}{suffix}")

    now = clock().isoformat(timespec="seconds")
    documents = DocumentRepository(conn)
    updated: list[str] = []
    with transaction(conn):
        for drive_id, fields in parsed:
            if documents.mark_manual(drive_id, fields):
                updated.append(drive_id)
            else:
                logger.warning("No document for drive id %s; row skipped", drive_id)
        ReviewQueueRepository(conn).resolve_for_drive_ids(updated, now)
    logger.info("Confirmed %d documents from %s", len(updated), path)
    return len(updated)
