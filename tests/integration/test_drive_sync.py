import sqlite3
from collections.abc import Iterator
from dataclasses import replace

import pytest

from unidex.db.repositories import RawFileRepository, iter_document_views
from unidex.db.seed import seed_courses
from unidex.exceptions import IngestionError
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.ingestion.compare import compare_listings
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.file_source import FileSource
from unidex.ingestion.sync_job import SyncJob
from unidex.models.raw_file import RawFile

from ..conftest import ALIASES_CSV, SAMPLE_CSV, fixed_clock

LABEL = "CS archive"


class ListSource(FileSource):
    def __init__(self, files: list[RawFile]) -> None:
        self._files = files

    def fetch(self) -> Iterator[RawFile]:
        yield from self._files


@pytest.fixture
def listing() -> list[RawFile]:
    return list(CsvFileSource(SAMPLE_CSV).fetch())


@pytest.fixture
def loaded(conn: sqlite3.Connection, listing: list[RawFile]) -> sqlite3.Connection:
    seed_courses(conn, ALIASES_CSV)
    SyncJob(conn, ListSource(listing), "CS", LABEL, clock=fixed_clock).run()
    ExtractionPipeline(conn, clock=fixed_clock).run()
    return conn


def later(minutes: int):
    from datetime import timedelta

    return lambda: fixed_clock() + timedelta(minutes=minutes)


def resync(conn: sqlite3.Connection, files: list[RawFile], minutes: int = 10, **kw: bool):
    return SyncJob(
        conn, ListSource(files), "CS", LABEL, clock=later(minutes), retire_missing=True, **kw
    ).run()


def visible(conn: sqlite3.Connection) -> int:
    return len(list(iter_document_views(conn)))


def test_a_file_that_vanished_is_hidden_not_deleted(
    loaded: sqlite3.Connection, listing: list[RawFile]
) -> None:
    before = visible(loaded)
    gone = next(f for f in listing if f.is_indexable)
    result = resync(loaded, [f for f in listing if f is not gone])
    assert result.retired == 1
    assert visible(loaded) == before - 1
    assert RawFileRepository(loaded).count() == len(listing)  # nothing deleted
    assert gone.drive_file_id not in {v.drive_file_id for v in iter_document_views(loaded)}


def test_a_returning_file_is_shown_again(
    loaded: sqlite3.Connection, listing: list[RawFile]
) -> None:
    before = visible(loaded)
    gone = next(f for f in listing if f.is_indexable)
    resync(loaded, [f for f in listing if f is not gone], minutes=10)
    result = resync(loaded, listing, minutes=20)
    assert result.restored == 1
    assert result.retired == 0
    assert visible(loaded) == before


def test_unchanged_listing_changes_nothing(
    loaded: sqlite3.Connection, listing: list[RawFile]
) -> None:
    before = visible(loaded)
    result = resync(loaded, listing)
    assert (result.retired, result.restored, result.inserted) == (0, 0, 0)
    assert visible(loaded) == before


def test_modified_file_is_updated_in_place(
    loaded: sqlite3.Connection, listing: list[RawFile]
) -> None:
    first = listing[0]
    renamed = replace(first, name="Renamed.pdf")
    result = resync(loaded, [renamed, *listing[1:]])
    assert result.inserted == 0
    row = loaded.execute(
        "SELECT name FROM raw_files WHERE drive_file_id = ?", (first.drive_file_id,)
    ).fetchone()
    assert row["name"] == "Renamed.pdf"


def test_a_suspiciously_small_listing_is_refused_and_changes_nothing(
    loaded: sqlite3.Connection, listing: list[RawFile]
) -> None:
    before = visible(loaded)
    with pytest.raises(IngestionError, match="partial listing"):
        resync(loaded, listing[:5])
    assert visible(loaded) == before
    assert RawFileRepository(loaded).count() == len(listing)


def test_force_accepts_a_small_listing(loaded: sqlite3.Connection, listing: list[RawFile]) -> None:
    result = resync(loaded, listing[:5], force=True)
    assert result.retired > 0
    assert visible(loaded) < len(listing)


def test_partial_csv_syncs_never_hide_anything(
    loaded: sqlite3.Connection, listing: list[RawFile]
) -> None:
    before = visible(loaded)
    SyncJob(loaded, ListSource(listing[:3]), "CS", LABEL, clock=later(5)).run()
    assert visible(loaded) == before


class TestCompare:
    def test_identical_listings(self, listing: list[RawFile]) -> None:
        result = compare_listings(listing, listing)
        assert result.in_both == len(listing)
        assert result.same_path == len(listing)
        assert result.only_new == result.only_old == 0

    def test_differences_are_counted_with_examples(self, listing: list[RawFile]) -> None:
        moved = replace(listing[0], path="/elsewhere")
        extra = replace(listing[1], drive_file_id="BRAND-NEW", name="new.pdf")
        new = [moved, extra, *listing[2:-1]]
        result = compare_listings(new, listing)
        assert result.in_both == len(listing) - 2  # listing[1] and the last one differ in id
        assert result.same_path == result.in_both - 1
        assert result.only_new == 1
        assert result.only_old == 2
        assert result.path_examples[0][1].startswith("/elsewhere")
        assert result.only_new_examples == (f"{extra.path}/new.pdf",)
