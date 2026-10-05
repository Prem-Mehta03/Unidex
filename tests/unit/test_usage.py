from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from unidex.api.usage import RateLimiter, UsageLog
from unidex.db.connection import connect, init_schema
from unidex.db.repositories import SearchLogRepository

START = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = START

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "usage.db"
    conn = connect(path)
    init_schema(conn)
    conn.close()
    return path


def logged(db_path: Path) -> list[tuple[str, str, int]]:
    conn = connect(db_path)
    try:
        rows = conn.execute("SELECT user_hash, query, result_count FROM search_logs ORDER BY id")
        return [(r["user_hash"], r["query"], r["result_count"]) for r in rows]
    finally:
        conn.close()


class TestSearchLogging:
    def test_a_search_is_stored_without_an_email(self, db_path: Path) -> None:
        log = UsageLog(db_path, Clock())
        log.search("abc123", "oop midsem", {"courses": ["CS F213"]}, 4, source="search")
        assert logged(db_path) == [("abc123", "oop midsem", 4)]

    def test_short_queries_without_filters_are_skipped(self, db_path: Path) -> None:
        log = UsageLog(db_path, Clock())
        log.search("u", "oo", {"courses": []}, 9, source="search")
        log.search("u", "", {}, 9, source="search")
        assert logged(db_path) == []

    def test_filters_alone_are_logged(self, db_path: Path) -> None:
        log = UsageLog(db_path, Clock())
        log.search("u", "", {"courses": ["CS F213"]}, 9, source="search")
        assert len(logged(db_path)) == 1

    def test_immediate_repeats_are_dropped_but_changes_are_kept(self, db_path: Path) -> None:
        log = UsageLog(db_path, Clock())
        for query in ("oop notes", "oop notes", "oop slides"):
            log.search("u", query, {}, 3, source="search")
        assert [row[1] for row in logged(db_path)] == ["oop notes", "oop slides"]

    def test_long_query_is_cut(self, db_path: Path) -> None:
        UsageLog(db_path, Clock()).search("u", "x" * 500, {}, 0, source="chat")
        assert len(logged(db_path)[0][1]) == 200

    def test_without_a_database_nothing_happens(self) -> None:
        log = UsageLog(None)
        log.search("u", "oop notes", {}, 1, source="search")
        assert not log.enabled
        assert log.report("id", "broken_link", "") is None

    def test_a_broken_database_never_raises(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.db"  # no tables
        UsageLog(empty, Clock()).search("u", "oop notes", {}, 1, source="search")


def test_old_logs_can_be_deleted(db_path: Path) -> None:
    clock = Clock()
    log = UsageLog(db_path, clock)
    log.search("u", "first query", {}, 1, source="search")
    clock.now = START + timedelta(days=40)
    log.search("u", "second query", {}, 1, source="search")
    conn = connect(db_path)
    removed = SearchLogRepository(conn).delete_before((START + timedelta(days=30)).isoformat())
    assert removed == 1
    assert SearchLogRepository(conn).count() == 1
    conn.close()


class TestRateLimiter:
    def test_blocks_after_the_limit_and_recovers(self) -> None:
        now = [0.0]
        limiter = RateLimiter(2, 60, clock=lambda: now[0])
        assert limiter.allow("a") and limiter.allow("a")
        assert not limiter.allow("a")
        assert limiter.allow("b")
        now[0] = 61
        assert limiter.allow("a")


def test_flat_values_for_the_durable_copy() -> None:
    from unidex.api.usage import _flat

    assert _flat(None) == ""
    assert _flat(["a", "b"]) == "a, b"
    assert _flat({"x"}) == "x"
    assert _flat(2025) == "2025"
