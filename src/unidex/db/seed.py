"""Seed reference data (departments, courses, aliases) from a CSV file.

The CSV has one row per alias::

    alias,course_code,course_name,department,verified
    OOP,CS F213,Object Oriented Programming,CS,false

Each course's own code and full name are also registered as aliases, so
``"CS F213"`` and ``"Object Oriented Programming"`` resolve without extra rows.
"""

import csv
import logging
import sqlite3
from pathlib import Path

from unidex.db.connection import transaction
from unidex.db.repositories import CourseRepository, DepartmentRepository
from unidex.exceptions import IngestionError

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("alias", "course_code", "course_name", "department", "verified")
_TRUE_VALUES = frozenset({"1", "true", "yes", "y"})


def seed_courses(conn: sqlite3.Connection, csv_path: Path) -> int:
    """Load courses and aliases from a CSV file in a single transaction.

    Safe to run repeatedly: existing courses and aliases are refreshed.

    Args:
        conn: An open connection with the schema already created.
        csv_path: Path to the alias CSV.

    Returns:
        The number of CSV rows processed.

    Raises:
        IngestionError: If the file is missing, lacks a required column, or two
            rows map one alias to different courses.
    """
    if not csv_path.is_file():
        raise IngestionError(f"Alias file not found: {csv_path}")

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestionError(f"{csv_path} is missing columns: {', '.join(missing)}")
        rows = list(reader)

    departments = DepartmentRepository(conn)
    courses = CourseRepository(conn)
    with transaction(conn):
        for row in rows:
            department_id = departments.get_or_create(row["department"].strip())
            course_id = courses.upsert(
                row["course_code"].strip(), row["course_name"].strip(), department_id
            )
            verified = row["verified"].strip().lower() in _TRUE_VALUES
            for alias in (row["alias"], row["course_code"], row["course_name"]):
                courses.add_alias(alias.strip(), course_id, verified)

    logger.info("Seeded %d alias rows from %s", len(rows), csv_path)
    return len(rows)
