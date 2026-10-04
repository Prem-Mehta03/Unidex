import sqlite3
from collections.abc import Iterator

import pytest

from unidex.db.repositories import RawFileRepository
from unidex.exceptions import IngestionError
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.file_source import FileSource
from unidex.ingestion.sync_job import SyncJob
from unidex.models.raw_file import RawFile

from ..conftest import SAMPLE_CSV, fixed_clock


class ExplodingSource(FileSource):
    """A source that fails part-way through its listing."""

    def fetch(self) -> Iterator[RawFile]:
        raise IngestionError("drive unreachable")
        yield  # pragma: no cover  (makes this a generator)


def run_sample_sync(conn: sqlite3.Connection) -> tuple[int, int, int, int]:
    job = SyncJob(conn, CsvFileSource(SAMPLE_CSV), "CS", "CS archive", clock=fixed_clock)
    result = job.run()
    return result.seen, result.inserted, result.updated, result.indexable


def test_sample_sync_loads_everything_and_flags_indexable(conn: sqlite3.Connection) -> None:
    seen, inserted, updated, indexable = run_sample_sync(conn)
    assert seen == 69
    assert (inserted, updated) == (69, 0)
    repo = RawFileRepository(conn)
    assert repo.count() == 69
    assert repo.count(indexable_only=True) == indexable
    assert 0 < indexable < seen  # code files exist and are excluded


def test_syncing_twice_is_idempotent(conn: sqlite3.Connection) -> None:
    run_sample_sync(conn)
    seen, inserted, updated, _ = run_sample_sync(conn)
    assert (seen, inserted, updated) == (69, 0, 69)
    assert RawFileRepository(conn).count() == 69


def test_sync_stamps_the_source(conn: sqlite3.Connection) -> None:
    run_sample_sync(conn)
    row = conn.execute("SELECT last_synced_at FROM sources").fetchone()
    assert row["last_synced_at"] == "2026-10-04T12:00:00+00:00"


def test_failing_source_leaves_database_untouched(conn: sqlite3.Connection) -> None:
    job = SyncJob(conn, ExplodingSource(), "CS", "CS archive", clock=fixed_clock)
    with pytest.raises(IngestionError):
        job.run()
    assert conn.execute("SELECT COUNT(*) AS n FROM departments").fetchone()["n"] == 0
    assert RawFileRepository(conn).count() == 0
