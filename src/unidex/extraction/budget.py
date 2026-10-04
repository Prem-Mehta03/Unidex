"""Keeps language-model usage inside the free-tier limits.

Two limits apply: requests per minute (we wait) and requests per day (we stop).
The daily count lives in the database so restarting a script cannot reset it.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from unidex.db.repositories import LlmUsageRepository
from unidex.exceptions import LLMBudgetExceededError

logger = logging.getLogger(__name__)

# Google resets free-tier daily quotas at midnight Pacific time.
QUOTA_TIME_ZONE = ZoneInfo("America/Los_Angeles")
SECONDS_PER_MINUTE = 60.0


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


class LlmBudget:
    """Counts requests and enforces the per-minute and per-day limits."""

    def __init__(
        self,
        usage: LlmUsageRepository,
        per_minute: int,
        per_day: int,
        now: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create a budget.

        Args:
            usage: Where the daily count is stored.
            per_minute: Requests allowed in any 60-second window.
            per_day: Requests allowed per quota day.
            now: Wall-clock time; replaced in tests.
            monotonic: Steady clock for the minute window; replaced in tests.
            sleep: Waits for the given seconds; replaced in tests.

        Raises:
            ValueError: If a limit is below 1.
        """
        if per_minute < 1 or per_day < 1:
            raise ValueError("per_minute and per_day must be at least 1")
        self._usage = usage
        self._per_minute = per_minute
        self._per_day = per_day
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._recent: deque[float] = deque()

    def _today(self) -> str:
        """Return today's date in the provider's quota time zone."""
        return self._now().astimezone(QUOTA_TIME_ZONE).date().isoformat()

    def remaining_today(self) -> int:
        """Return how many requests may still be made today."""
        return max(0, self._per_day - self._usage.requests_on(self._today()))

    def acquire(self) -> None:
        """Reserve one request, waiting for the minute window if needed.

        The request is counted before it is sent, so a request that fails
        still uses up budget (providers count failed calls too).

        Raises:
            LLMBudgetExceededError: If today's allowance is used up.
        """
        day = self._today()
        used = self._usage.requests_on(day)
        if used >= self._per_day:
            raise LLMBudgetExceededError(
                f"Daily language-model budget used up ({used}/{self._per_day}); "
                "remaining files stay in the review queue"
            )
        self._wait_for_minute_slot()
        total = self._usage.record_request(day)
        logger.info("Language-model request %d of %d allowed today", total, self._per_day)

    def _wait_for_minute_slot(self) -> None:
        """Block until fewer than ``per_minute`` requests happened in the last minute."""
        while True:
            now = self._monotonic()
            while self._recent and now - self._recent[0] >= SECONDS_PER_MINUTE:
                self._recent.popleft()
            if len(self._recent) < self._per_minute:
                self._recent.append(now)
                return
            wait = SECONDS_PER_MINUTE - (now - self._recent[0])
            logger.info("Per-minute limit reached; waiting %.0f seconds", wait)
            self._sleep(max(wait, 0.0))
