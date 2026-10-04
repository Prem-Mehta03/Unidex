import sqlite3
from pathlib import Path

import pytest

from unidex.db.connection import SCHEMA_VERSION, connect, init_schema
from unidex.exceptions import DatabaseError

# The documents table exactly as Stage 1 created it.
V1_DOCUMENTS = """
CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    raw_file_id INTEGER NOT NULL UNIQUE,
    title TEXT NOT NULL,
    exam_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (exam_type IN ('quiz', 'midsem', 'compre', 'lab_compre', 'none', 'unknown')),
    created_at TEXT NOT NULL
);
"""


def make_v1_database(path: Path, with_row: bool) -> None:
    raw = sqlite3.connect(path)
    raw.executescript(V1_DOCUMENTS)
    if with_row:
        raw.execute("INSERT INTO documents (raw_file_id, title, created_at) VALUES (1, 't', 'now')")
    raw.commit()
    raw.close()


def test_fresh_database_gets_the_current_version() -> None:
    conn = connect(":memory:")
    init_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_old_empty_documents_table_is_replaced(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    make_v1_database(db, with_row=False)
    conn = connect(db)
    init_schema(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"has_solution", "syllabus_scope", "extraction_notes"} <= columns
    # The new list of allowed values includes 'test'; the old one did not.
    raw_id = conn.execute("SELECT 1").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO documents (raw_file_id, title, drive_url, mime_type, exam_type, created_at)"
        " VALUES (?, 't', 'u', 'm', 'test', 'now')",
        (raw_id,),
    )
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_old_table_with_data_is_never_dropped(tmp_path: Path) -> None:
    db = tmp_path / "old.db"
    make_v1_database(db, with_row=True)
    conn = connect(db)
    with pytest.raises(DatabaseError, match="1 rows"):
        init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1


def test_running_init_twice_is_harmless() -> None:
    conn = connect(":memory:")
    init_schema(conn)
    init_schema(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
