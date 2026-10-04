import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from unidex.db.repositories import LlmUsageRepository
from unidex.exceptions import LLMBudgetExceededError
from unidex.extraction.budget import LlmBudget


class FakeClock:
    """A controllable clock; sleeping simply moves time forward."""

    def __init__(self, start: datetime) -> None:
        self.wall = start
        self.steady = 1000.0
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.wall

    def monotonic(self) -> float:
        return self.steady

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.steady += seconds
        self.wall += timedelta(seconds=seconds)


def make_budget(
    conn: sqlite3.Connection, clock: FakeClock, per_minute: int = 5, per_day: int = 20
) -> LlmBudget:
    return LlmBudget(
        LlmUsageRepository(conn),
        per_minute,
        per_day,
        now=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


NOON_UTC = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)


def test_requests_are_counted_in_the_database(conn: sqlite3.Connection) -> None:
    clock = FakeClock(NOON_UTC)
    budget = make_budget(conn, clock)
    budget.acquire()
    budget.acquire()
    assert budget.remaining_today() == 18
    assert LlmUsageRepository(conn).requests_on("2026-10-04") == 2


def test_count_survives_a_new_budget_object(conn: sqlite3.Connection) -> None:
    clock = FakeClock(NOON_UTC)
    make_budget(conn, clock).acquire()
    assert make_budget(conn, clock).remaining_today() == 19


def test_daily_limit_raises_before_sending(conn: sqlite3.Connection) -> None:
    clock = FakeClock(NOON_UTC)
    budget = make_budget(conn, clock, per_minute=100, per_day=3)
    for _ in range(3):
        budget.acquire()
    with pytest.raises(LLMBudgetExceededError):
        budget.acquire()
    assert LlmUsageRepository(conn).requests_on("2026-10-04") == 3


def test_sixth_request_in_a_minute_waits(conn: sqlite3.Connection) -> None:
    clock = FakeClock(NOON_UTC)
    budget = make_budget(conn, clock)
    for _ in range(5):
        budget.acquire()
    assert clock.slept == []
    budget.acquire()
    assert len(clock.slept) == 1
    assert clock.slept[0] == pytest.approx(60.0)


def test_no_wait_when_requests_are_spread_out(conn: sqlite3.Connection) -> None:
    clock = FakeClock(NOON_UTC)
    budget = make_budget(conn, clock)
    for _ in range(12):
        budget.acquire()
        clock.steady += 13  # more than 60 / 5 seconds apart
    assert clock.slept == []


def test_quota_day_follows_pacific_time(conn: sqlite3.Connection) -> None:
    # 06:00 UTC on Oct 5 is still 23:00 on Oct 4 in Pacific time (UTC-7 in October).
    clock = FakeClock(datetime(2026, 10, 5, 6, 0, tzinfo=UTC))
    make_budget(conn, clock).acquire()
    assert LlmUsageRepository(conn).requests_on("2026-10-04") == 1
    clock.wall = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)  # 01:00 Oct 5 Pacific
    assert make_budget(conn, clock).remaining_today() == 20


@pytest.mark.parametrize(("per_minute", "per_day"), [(0, 5), (5, 0), (-1, 1)])
def test_limits_must_be_positive(conn: sqlite3.Connection, per_minute: int, per_day: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        LlmBudget(LlmUsageRepository(conn), per_minute, per_day)
