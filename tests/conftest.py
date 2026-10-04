import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from unidex.db.connection import connect, init_schema

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_CSV = REPO_ROOT / "data" / "sample" / "sample_drive.csv"
ALIASES_CSV = REPO_ROOT / "data" / "course_aliases.csv"
FIXED_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    """A fresh in-memory database with the schema created."""
    connection = connect(":memory:")
    init_schema(connection)
    yield connection
    connection.close()


def fixed_clock() -> datetime:
    return FIXED_NOW
