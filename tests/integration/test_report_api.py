from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from unidex.api.app import create_app
from unidex.api.usage import UsageLog
from unidex.config import Settings
from unidex.db.connection import connect, init_schema
from unidex.db.repositories import ReportRepository, iter_document_views, load_alias_map
from unidex.db.seed import seed_courses
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.sync_job import SyncJob
from unidex.search.catalog import Catalog

from ..conftest import ALIASES_CSV, SAMPLE_CSV, fixed_clock


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "reports.db"
    conn = connect(path)
    init_schema(conn)
    seed_courses(conn, ALIASES_CSV)
    SyncJob(conn, CsvFileSource(SAMPLE_CSV), "CS", "CS archive", clock=fixed_clock).run()
    ExtractionPipeline(conn, clock=fixed_clock).run()
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    conn = connect(db_path)
    catalog = Catalog(list(iter_document_views(conn)), course_aliases=load_alias_map(conn))
    conn.close()
    settings = Settings(db_path=db_path, log_level="INFO")
    with TestClient(create_app(catalog=catalog, settings=settings, web_dir=None)) as c:
        yield c


def first_file_id(client: TestClient) -> str:
    card = client.get("/api/search?q=oop").json()["stacks"][0]["files"][0]
    return str(card["id"])


def count_reports(db_path: Path) -> list[tuple[str, str | None]]:
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT type, note FROM reports ORDER BY id").fetchall()
        return [(r["type"], r["note"]) for r in rows]
    finally:
        conn.close()


def test_a_report_is_stored(client: TestClient, db_path: Path) -> None:
    file_id = first_file_id(client)
    response = client.post(
        "/api/report", json={"file_id": file_id, "type": "wrong_info", "note": "  Year is 2022  "}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "recorded"}
    assert count_reports(db_path) == [("wrong_info", "Year is 2022")]


def test_note_is_optional(client: TestClient, db_path: Path) -> None:
    client.post("/api/report", json={"file_id": first_file_id(client), "type": "broken_link"})
    assert count_reports(db_path) == [("broken_link", None)]


def test_same_report_twice_is_stored_once(client: TestClient, db_path: Path) -> None:
    body = {"file_id": first_file_id(client), "type": "broken_link"}
    assert client.post("/api/report", json=body).json()["status"] == "recorded"
    assert client.post("/api/report", json=body).json()["status"] == "already_reported"
    assert len(count_reports(db_path)) == 1


def test_different_types_on_one_file_are_both_kept(client: TestClient, db_path: Path) -> None:
    file_id = first_file_id(client)
    client.post("/api/report", json={"file_id": file_id, "type": "broken_link"})
    client.post("/api/report", json={"file_id": file_id, "type": "wrong_info"})
    assert len(count_reports(db_path)) == 2


def test_unknown_file_is_a_404(client: TestClient) -> None:
    response = client.post("/api/report", json={"file_id": "nope", "type": "broken_link"})
    assert response.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"file_id": "", "type": "broken_link"},
        {"file_id": "x", "type": "spam"},
        {"file_id": "x", "type": "broken_link", "note": "n" * 501},
        {"type": "broken_link"},
    ],
)
def test_bad_requests_are_rejected(client: TestClient, body: dict) -> None:
    assert client.post("/api/report", json=body).status_code == 422


def test_report_flood_is_limited(client: TestClient) -> None:
    file_id = first_file_id(client)
    statuses = [
        client.post("/api/report", json={"file_id": file_id, "type": "wrong_info"}).status_code
        for _ in range(12)
    ]
    assert statuses.count(429) == 2


def test_reports_are_unavailable_without_a_database() -> None:
    catalog = Catalog([])
    with TestClient(create_app(catalog=catalog, web_dir=None)) as c:
        response = c.post("/api/report", json={"file_id": "x", "type": "broken_link"})
        assert response.status_code == 503


def test_old_duplicate_does_not_block_a_new_report(db_path: Path) -> None:
    conn = connect(db_path)
    row = conn.execute("SELECT drive_file_id FROM raw_files LIMIT 1").fetchone()
    drive_id = row["drive_file_id"]
    now = datetime(2026, 10, 5, tzinfo=UTC)
    repo = ReportRepository(conn)
    stamp = now.isoformat(timespec="seconds")
    since = (now - timedelta(hours=24)).isoformat(timespec="seconds")
    old = (now - timedelta(days=3)).isoformat(timespec="seconds")
    first = repo.add(drive_id, "broken_link", "", old, duplicate_since=since)
    second = repo.add(drive_id, "broken_link", "", stamp, duplicate_since=since)
    assert first is not None and second is not None
    assert first[1] is True and second[1] is True
    assert [r.drive_file_id for r in repo.recent()] == [drive_id, drive_id]
    conn.close()


def test_usage_log_report_roundtrip(db_path: Path) -> None:
    conn = connect(db_path)
    drive_id = conn.execute("SELECT drive_file_id FROM raw_files LIMIT 1").fetchone()[0]
    conn.close()
    log = UsageLog(db_path)
    assert log.report(drive_id, "broken_link", "") is not None
    assert log.report("missing", "broken_link", "") is None


def test_chat_final_search_is_logged_but_estimates_are_not(
    client: TestClient, db_path: Path
) -> None:
    form = client.post("/api/chat/message", json={"message": "OOP midsem papers"}).json()
    body = {"interpretation": form["interpretation"]}
    client.post("/api/chat/results", json=body)
    conn = connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM search_logs").fetchone()[0] == 0
    client.post("/api/chat/results", json={**body, "final": True})
    rows = conn.execute("SELECT filters, result_count FROM search_logs").fetchall()
    conn.close()
    assert len(rows) == 1
    assert '"source": "chat"' in rows[0]["filters"]
