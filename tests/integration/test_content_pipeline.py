import sqlite3
from datetime import timedelta

import pytest

from tests.integration.test_drive_sync import ListSource  # noqa: F401  (reused fixture helpers)
from tests.unit._pdf_data import LONG, make_pdf
from tests.unit.test_content import FakeEngine, scanned_pdf
from unidex.content.pipeline import ContentPipeline
from unidex.content.readers import OcrReader
from unidex.db.repositories import (
    DocumentTextRepository,
    iter_document_views,
    load_searchable_texts,
)
from unidex.db.seed import seed_courses
from unidex.exceptions import IngestionError
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.sync_job import SyncJob

from ..conftest import ALIASES_CSV, SAMPLE_CSV, fixed_clock

GARBLED = "Q1)ProofbyStructuralInduction Base Step: A=pelP length/A)=1 isodd (Mark InductionStep:"
TYPES = ("pyq",)


@pytest.fixture
def loaded(conn: sqlite3.Connection) -> sqlite3.Connection:
    seed_courses(conn, ALIASES_CSV)
    SyncJob(conn, CsvFileSource(SAMPLE_CSV), "CS", "CS archive", clock=fixed_clock).run()
    ExtractionPipeline(conn, clock=fixed_clock).run()
    return conn


class FakeDownloader:
    """Hands out a different kind of file for each drive id, in a fixed order."""

    def __init__(self, files: dict[str, bytes | Exception]) -> None:
        self.files = files
        self.requested: list[str] = []

    def download(self, file_id: str) -> bytes:
        self.requested.append(file_id)
        result = self.files[file_id]
        if isinstance(result, Exception):
            raise result
        return result


def pending_ids(conn: sqlite3.Connection) -> list[str]:
    return [t.drive_file_id for t in DocumentTextRepository(conn).pending(TYPES)]


def rows(conn: sqlite3.Connection) -> dict[str, tuple[str, float]]:
    found = conn.execute(
        """
        SELECT r.drive_file_id, t.method, t.quality FROM document_text t
        JOIN documents d ON d.id = t.document_id JOIN raw_files r ON r.id = d.raw_file_id
        """
    ).fetchall()
    return {r["drive_file_id"]: (r["method"], r["quality"]) for r in found}


def test_every_kind_of_pdf_is_handled(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)
    assert len(ids) >= 6
    plan: dict[str, bytes | Exception] = {
        ids[0]: make_pdf([LONG, LONG]),  # clean text layer
        ids[1]: scanned_pdf(),  # a scan: OCR needed
        ids[2]: make_pdf(["", ""]),  # blank pages and no OCR engine result
        ids[3]: make_pdf([GARBLED * 2]),  # handwriting-like text
        ids[4]: b"not a pdf",  # unreadable
        ids[5]: IngestionError("Drive said no"),  # download fails
    }
    for other in ids[6:]:
        plan[other] = make_pdf([LONG])
    pipeline = ContentPipeline(
        loaded,
        FakeDownloader(plan),
        ocr=OcrReader(FakeEngine()),
        clock=fixed_clock,
    )
    report = pipeline.run(TYPES)
    stored = rows(loaded)
    assert stored[ids[0]][0] == "pdf_text"
    assert stored[ids[1]][0] == "none"  # fake engine's "page of size 1700" is too short a text
    assert stored[ids[2]][0] == "none"
    assert stored[ids[3]][0] == "none"
    assert stored[ids[4]] == ("none", 0.0)
    assert ids[5] not in stored  # a failed download is retried next time, not remembered
    assert report.failed == 1
    assert report.processed == len(ids)
    assert report.from_text == 1 + len(ids[6:])
    assert report.unreadable == 4


def test_ocr_text_is_kept_when_it_is_good(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)

    class WordyEngine:
        def recognize(self, image: object) -> str:
            return LONG

    plan = {i: scanned_pdf() for i in ids}
    report = ContentPipeline(
        loaded, FakeDownloader(plan), ocr=OcrReader(WordyEngine()), clock=fixed_clock
    ).run(TYPES, limit=2)
    assert report.from_ocr == 2
    stored = rows(loaded)
    assert all(method == "ocr" for method, _ in stored.values())
    texts = load_searchable_texts(loaded, 0.65)
    assert any("inheritance" in text for text in texts.values())


def test_without_ocr_a_scan_is_remembered_as_unreadable(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)
    report = ContentPipeline(
        loaded, FakeDownloader({i: scanned_pdf() for i in ids}), clock=fixed_clock
    ).run(TYPES)
    assert report.unreadable == len(ids)
    assert report.from_ocr == 0


def test_a_second_run_only_does_new_work(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)
    downloader = FakeDownloader({i: make_pdf([LONG]) for i in ids})
    pipeline = ContentPipeline(loaded, downloader, clock=fixed_clock)
    pipeline.run(TYPES)
    first_requests = len(downloader.requested)
    again = pipeline.run(TYPES)
    assert again.processed == 0
    assert len(downloader.requested) == first_requests
    forced = pipeline.run(TYPES, force=True)
    assert forced.processed == len(ids)


def test_a_changed_file_is_read_again(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)
    pipeline = ContentPipeline(
        loaded, FakeDownloader({i: make_pdf([LONG]) for i in ids}), clock=fixed_clock
    )
    pipeline.run(TYPES)
    loaded.execute(
        "UPDATE raw_files SET modified_on = '2030-01-01' WHERE drive_file_id = ?", (ids[0],)
    )
    assert pending_ids(loaded) == [ids[0]]


def test_limit_and_types(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)
    pipeline = ContentPipeline(
        loaded, FakeDownloader({i: make_pdf([LONG]) for i in ids}), clock=fixed_clock
    )
    assert pipeline.run(TYPES, limit=3).processed == 3
    assert pipeline.run((), limit=3).processed == 0


def test_hidden_documents_are_not_read_or_loaded(loaded: sqlite3.Connection) -> None:
    ids = pending_ids(loaded)
    ContentPipeline(
        loaded, FakeDownloader({i: make_pdf([LONG]) for i in ids}), clock=fixed_clock
    ).run(TYPES)
    loaded.execute("UPDATE documents SET link_status = 'broken'")
    assert load_searchable_texts(loaded, 0.0) == {}
    assert pending_ids(loaded) == []


def test_catalog_receives_the_stored_text(loaded: sqlite3.Connection) -> None:
    from unidex.search.catalog import Catalog

    ids = pending_ids(loaded)
    ContentPipeline(
        loaded, FakeDownloader({i: make_pdf([LONG]) for i in ids}), clock=fixed_clock
    ).run(TYPES)
    texts = load_searchable_texts(loaded, 0.65)
    catalog = Catalog(list(iter_document_views(loaded)), texts=texts)
    assert catalog.has_content
    result = catalog.search_content("inheritance polymorphism")
    assert len(result.matched) == len(ids)
    assert result.matched[0].matched_terms == ("inheritance", "polymorphism")
    assert timedelta(0) == timedelta(0)
