from pathlib import Path

import pytest

from unidex.db.connection import connect, init_schema
from unidex.db.export import ACTIVITY_TABLES, ExportError, export_snapshot


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "work.db"
    conn = connect(path)
    init_schema(conn)
    conn.execute(
        "INSERT INTO search_logs(user_hash, query, filters, result_count, created_at)"
        " VALUES ('u', 'secret query', '{}', 1, '2026-01-01')"
    )
    conn.execute("INSERT INTO llm_usage(day, requests) VALUES ('2026-01-01', 3)")
    conn.close()
    return path


def count(path: Path, table: str) -> int:
    conn = connect(path)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # noqa: S608
    finally:
        conn.close()


def test_snapshot_drops_activity_but_keeps_the_schema(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "deploy" / "catalog.db"
    info = export_snapshot(source, target)
    assert info.path == target
    assert info.size_bytes > 0
    for table in ACTIVITY_TABLES:
        assert count(target, table) == 0  # the table exists but holds nothing
    assert count(source, "search_logs") == 1  # the original is untouched


def test_snapshot_replaces_an_older_one(source: Path, tmp_path: Path) -> None:
    target = tmp_path / "catalog.db"
    export_snapshot(source, target)
    export_snapshot(source, target)
    assert target.is_file()


def test_bad_sources_are_refused(tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="not found"):
        export_snapshot(tmp_path / "missing.db", tmp_path / "out.db")
    empty = tmp_path / "empty.db"
    empty.touch()
    with pytest.raises(ExportError, match="empty"):
        export_snapshot(empty, tmp_path / "out.db")


def test_snapshot_cannot_overwrite_the_database(source: Path) -> None:
    with pytest.raises(ExportError, match="different"):
        export_snapshot(source, source)
