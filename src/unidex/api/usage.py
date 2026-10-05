"""Usage logging and report taking, kept apart from the request handlers.

Logging must never break a search, so every method here catches database
errors, writes a warning and carries on.
"""

import json
import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from unidex.db.connection import connect
from unidex.db.repositories import ReportRepository, SearchLogRepository

logger = logging.getLogger(__name__)

MAX_LOGGED_QUERY = 200
MIN_LOGGED_QUERY = 3
REPEAT_WINDOW_SECONDS = 10.0
DUPLICATE_REPORT_WINDOW = timedelta(hours=24)


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


class RateLimiter:
    """Allow at most ``limit`` events per person in a sliding window."""

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a limiter.

        Args:
            limit: Events allowed per window.
            window_seconds: Length of the window.
            clock: Returns seconds; tests pass a fake one.
        """
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, who: str) -> bool:
        """Count an event for ``who`` and say whether it is allowed.

        Args:
            who: Any stable label for the person.

        Returns:
            ``False`` if the person already used up the window.
        """
        now = self._clock()
        with self._lock:
            events = self._events.setdefault(who, deque())
            while events and events[0] <= now - self._window:
                events.popleft()
            if len(events) >= self._limit:
                return False
            events.append(now)
            return True


class UsageLog:
    """Writes search logs and reports into the database (or does nothing without one)."""

    def __init__(self, db_path: Path | str | None, clock: Callable[[], datetime] = utc_now) -> None:
        """Create the logger.

        Args:
            db_path: Database file, or ``None`` to switch logging and reports off.
            clock: Returns the current UTC time; tests pass a fixed one.
        """
        self._db_path = db_path
        self._clock = clock
        self._last: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        """Whether a database is attached."""
        return self._db_path is not None

    def search(
        self,
        user: str,
        query: str,
        filters: Mapping[str, Any],
        result_count: int,
        source: str,
    ) -> None:
        """Log one search; short or immediately repeated queries are skipped.

        Args:
            user: Non-reversible label for the person.
            query: The text searched for.
            filters: Filters in use (JSON-friendly values).
            result_count: Files matched.
            source: ``search`` (the Search tab) or ``chat``.
        """
        if self._db_path is None:
            return
        text = query.strip()[:MAX_LOGGED_QUERY]
        has_filters = any(filters.values())
        if len(text) < MIN_LOGGED_QUERY and not has_filters:
            return
        key = json.dumps([text, sorted(filters.items()), source], default=str)
        now = time.monotonic()
        with self._lock:
            previous = self._last.get(user)
            if previous and previous[0] == key and now - previous[1] < REPEAT_WINDOW_SECONDS:
                return
            self._last[user] = (key, now)
        payload = json.dumps({"source": source, **filters}, default=str, sort_keys=True)
        try:
            conn = connect(self._db_path)
            try:
                SearchLogRepository(conn).add(
                    user, text, payload, result_count, _stamp(self._clock())
                )
            finally:
                conn.close()
        except sqlite3.Error:
            logger.warning("Could not write the search log", exc_info=True)

    def report(self, drive_file_id: str, report_type: str, note: str) -> tuple[int, bool] | None:
        """Store a report about a file.

        Args:
            drive_file_id: Drive id of the file.
            report_type: ``wrong_info`` or ``broken_link``.
            note: Optional explanation.

        Returns:
            ``(report id, is_new)``; ``None`` if the file is unknown.

        Raises:
            sqlite3.Error: If the database cannot be written (the caller reports a failure).
        """
        if self._db_path is None:
            return None
        now = self._clock()
        conn = connect(self._db_path)
        try:
            return ReportRepository(conn).add(
                drive_file_id,
                report_type,
                note.strip(),
                _stamp(now),
                duplicate_since=_stamp(now - DUPLICATE_REPORT_WINDOW),
            )
        finally:
            conn.close()
