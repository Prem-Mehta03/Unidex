"""Send reports and search logs somewhere that survives a restart.

Free hosts wipe the server's disk whenever the service restarts or goes to sleep, so the
SQLite tables for reports and search logs would be lost. An :class:`EventSink` copies each
event to a durable place. The shipped implementation posts JSON to a small Google Apps Script
web app that appends rows to a Google Sheet (see ``deploy/apps_script_sink.gs``).

Sinks never raise into a request: a failure is logged and the event is dropped, because a
search must not fail just because a log could not be saved.
"""

import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx2

logger = logging.getLogger(__name__)

DEFAULT_FLUSH_SECONDS = 10.0
MAX_BUFFERED_EVENTS = 1000
MAX_BATCH = 50
ATTEMPTS = 3
REQUEST_TIMEOUT_SECONDS = 20.0


class EventSink(ABC):
    """Anything that can keep a record of events."""

    @abstractmethod
    def send(self, kind: str, data: Mapping[str, Any], *, urgent: bool = False) -> None:
        """Record one event.

        Args:
            kind: Event name, such as ``search`` or ``report``.
            data: JSON-friendly details.
            urgent: Send soon instead of waiting for the next batch.
        """

    def close(self) -> None:  # noqa: B027
        """Send anything still waiting. The default does nothing."""


class NullSink(EventSink):
    """Drops every event (local development, or no destination configured)."""

    def send(self, kind: str, data: Mapping[str, Any], *, urgent: bool = False) -> None:
        """Ignore the event."""


class WebhookSink(EventSink):
    """Batches events and posts them as JSON to a web address."""

    def __init__(
        self,
        url: str,
        secret: str,
        http: httpx2.Client | None = None,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        autostart: bool = True,
    ) -> None:
        """Create a sink.

        Args:
            url: Where to post (an Apps Script web app address).
            secret: Shared secret placed in each post so the receiver can reject strangers.
            http: HTTP client; tests pass one with a fake transport.
            flush_seconds: How long events may wait before being sent.
            clock: Returns the current UTC time.
            autostart: Start the background sender. Tests turn this off and call :meth:`flush`.
        """
        self._url = url
        self._secret = secret
        self._http = http or httpx2.Client(timeout=REQUEST_TIMEOUT_SECONDS, follow_redirects=True)
        self._flush_seconds = flush_seconds
        self._clock = clock
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if autostart:
            self._thread = threading.Thread(target=self._run, name="event-sink", daemon=True)
            self._thread.start()

    def send(self, kind: str, data: Mapping[str, Any], *, urgent: bool = False) -> None:
        """Queue an event; it is posted by the background sender."""
        event = {
            "kind": kind,
            "at": self._clock().isoformat(timespec="seconds"),
            "data": dict(data),
        }
        with self._lock:
            if len(self._events) >= MAX_BUFFERED_EVENTS:
                logger.warning("Event buffer full; dropping a %s event", kind)
                return
            self._events.append(event)
            full = len(self._events) >= MAX_BATCH
        if urgent or full:
            self._wake.set()

    def flush(self) -> int:
        """Post everything queued, in batches.

        Returns:
            How many events were delivered. Events that fail after retries are dropped.
        """
        delivered = 0
        while True:
            with self._lock:
                batch, self._events = self._events[:MAX_BATCH], self._events[MAX_BATCH:]
            if not batch:
                return delivered
            if self._post(batch):
                delivered += len(batch)
            else:
                logger.warning("Dropping %d events that could not be delivered", len(batch))

    def close(self) -> None:
        """Stop the background sender and send what is left."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=REQUEST_TIMEOUT_SECONDS)
        self.flush()

    def _run(self) -> None:
        """Background loop: wait, then flush."""
        while not self._stop.is_set():
            self._wake.wait(timeout=self._flush_seconds)
            self._wake.clear()
            try:
                self.flush()
            except Exception:  # the loop must survive anything
                logger.exception("Unexpected error while sending events")

    def _post(self, batch: list[dict[str, Any]]) -> bool:
        """Post one batch with a few attempts; return whether it was accepted."""
        body = {"secret": self._secret, "events": batch}
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = self._http.post(self._url, json=body)
                if response.status_code == httpx2.codes.OK and response.text.strip() == "ok":
                    return True
                logger.warning(
                    "Event receiver answered %s (attempt %d)", response.status_code, attempt
                )
            except httpx2.HTTPError:
                logger.warning("Could not reach the event receiver (attempt %d)", attempt)
            if attempt < ATTEMPTS:
                self._stop.wait(timeout=2.0**attempt)
        return False


def build_sink(url: str | None, secret: str | None) -> EventSink:
    """Create the sink the settings ask for.

    Args:
        url: Receiver address, or ``None``.
        secret: Shared secret, or ``None``.

    Returns:
        A :class:`WebhookSink` when both are set, otherwise a :class:`NullSink`.
    """
    if url and secret:
        logger.info("Reports and search logs are also sent to the event receiver")
        return WebhookSink(url, secret)
    return NullSink()
