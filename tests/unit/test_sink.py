import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

from unidex.api import sink as sink_module
from unidex.api.sink import NullSink, WebhookSink, build_sink
from unidex.api.usage import UsageLog
from unidex.config import Settings
from unidex.db.connection import connect, init_schema
from unidex.exceptions import ConfigError

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


class Receiver:
    """A fake Apps Script: records posts, answers according to a script of replies."""

    def __init__(self, replies: list[tuple[int, str]] | None = None) -> None:
        self.posts: list[dict[str, object]] = []
        self.replies = list(replies or [])

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.posts.append(json.loads(request.content))
        status, text = self.replies.pop(0) if self.replies else (200, "ok")
        return httpx2.Response(status, text=text)


def make_sink(receiver: Receiver) -> WebhookSink:
    client = httpx2.Client(transport=httpx2.MockTransport(receiver))
    return WebhookSink(
        "https://example.test/exec", "s3cret", http=client, clock=lambda: NOW, autostart=False
    )


def events(receiver: Receiver) -> list[dict[str, object]]:
    return [e for post in receiver.posts for e in post["events"]]  # type: ignore[union-attr]


def test_events_are_posted_in_one_batch_with_the_secret() -> None:
    receiver = Receiver()
    sink = make_sink(receiver)
    sink.send("search", {"query": "oop"})
    sink.send("report", {"file_id": "f1"})
    assert receiver.posts == []  # nothing is sent until a flush
    assert sink.flush() == 2
    assert len(receiver.posts) == 1
    assert receiver.posts[0]["secret"] == "s3cret"
    assert [e["kind"] for e in events(receiver)] == ["search", "report"]
    assert events(receiver)[0]["at"] == "2026-10-05T12:00:00+00:00"
    assert sink.flush() == 0  # already sent


def test_large_backlogs_are_split_into_batches() -> None:
    receiver = Receiver()
    sink = make_sink(receiver)
    for i in range(sink_module.MAX_BATCH + 5):
        sink.send("search", {"n": i})
    assert sink.flush() == sink_module.MAX_BATCH + 5
    assert [len(p["events"]) for p in receiver.posts] == [sink_module.MAX_BATCH, 5]  # type: ignore[arg-type]


def test_a_rejected_post_is_retried_then_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    receiver = Receiver([(200, "forbidden")] * sink_module.ATTEMPTS)
    sink = make_sink(receiver)
    monkeypatch.setattr(sink._stop, "wait", lambda timeout: False)  # no real sleeping
    sink.send("search", {"query": "x"})
    assert sink.flush() == 0
    assert len(receiver.posts) == sink_module.ATTEMPTS
    assert sink.flush() == 0  # dropped, not kept forever


def test_a_temporary_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    receiver = Receiver([(500, "oops"), (200, "ok")])
    sink = make_sink(receiver)
    monkeypatch.setattr(sink._stop, "wait", lambda timeout: False)
    sink.send("report", {"file_id": "f"})
    assert sink.flush() == 1
    assert len(receiver.posts) == 2


def test_network_errors_never_escape(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    sink = WebhookSink(
        "https://example.test/exec",
        "x",
        http=httpx2.Client(transport=httpx2.MockTransport(broken)),
        autostart=False,
    )
    monkeypatch.setattr(sink._stop, "wait", lambda timeout: False)
    sink.send("search", {})
    assert sink.flush() == 0


def test_a_full_buffer_drops_new_events() -> None:
    receiver = Receiver()
    sink = make_sink(receiver)
    for _ in range(sink_module.MAX_BUFFERED_EVENTS + 10):
        sink.send("search", {})
    assert sink.flush() == sink_module.MAX_BUFFERED_EVENTS


def test_close_sends_what_is_left() -> None:
    receiver = Receiver()
    sink = make_sink(receiver)
    sink.send("search", {"query": "last words"})
    sink.close()
    assert len(events(receiver)) == 1


def test_the_background_sender_delivers_urgent_events() -> None:
    receiver = Receiver()
    client = httpx2.Client(transport=httpx2.MockTransport(receiver))
    sink = WebhookSink("https://example.test/exec", "x", http=client, flush_seconds=60)
    sink.send("report", {"file_id": "f"}, urgent=True)
    sink.close()  # joins the thread, so delivery has happened
    assert len(events(receiver)) == 1


def test_build_sink_needs_both_values() -> None:
    assert isinstance(build_sink(None, None), NullSink)
    assert isinstance(build_sink("https://x.test/exec", None), NullSink)
    real = build_sink("https://x.test/exec", "s")
    assert isinstance(real, WebhookSink)
    real.close()


class TestUsageLogUsesTheSink:
    @pytest.fixture
    def parts(self, tmp_path: Path) -> tuple[UsageLog, Receiver, WebhookSink]:
        path = tmp_path / "u.db"
        conn = connect(path)
        init_schema(conn)
        conn.close()
        receiver = Receiver()
        sink = make_sink(receiver)
        return UsageLog(path, clock=lambda: NOW, sink=sink), receiver, sink

    def test_a_logged_search_is_copied(self, parts: tuple[UsageLog, Receiver, WebhookSink]) -> None:
        usage, receiver, sink = parts
        usage.search("u1", "laplace papers", {"courses": ["MATH F211"], "years": [2025]}, 4, "chat")
        sink.flush()
        data = events(receiver)[0]["data"]
        assert data["query"] == "laplace papers"  # type: ignore[index]
        assert data["courses"] == "MATH F211"  # type: ignore[index]
        assert data["results"] == 4  # type: ignore[index]
        assert "@" not in json.dumps(receiver.posts)

    def test_skipped_searches_are_not_copied(
        self, parts: tuple[UsageLog, Receiver, WebhookSink]
    ) -> None:
        usage, receiver, sink = parts
        usage.search("u1", "ab", {}, 0, "search")  # too short
        usage.search("u1", "graphs", {}, 1, "search")
        usage.search("u1", "graphs", {}, 1, "search")  # immediate repeat
        sink.flush()
        assert len(events(receiver)) == 1


def test_webhook_settings_must_come_together() -> None:
    with pytest.raises(ConfigError, match="together"):
        Settings.from_mapping({"EVENT_WEBHOOK_URL": "https://x.test/exec"})
    with pytest.raises(ConfigError, match="https"):
        Settings.from_mapping({"EVENT_WEBHOOK_URL": "http://x.test", "EVENT_WEBHOOK_SECRET": "s"})
    ok = Settings.from_mapping(
        {"EVENT_WEBHOOK_URL": "https://x.test/exec", "EVENT_WEBHOOK_SECRET": "s"}
    )
    assert ok.event_webhook_url == "https://x.test/exec"
    assert "s'" not in repr(ok).replace("'s'", "")  # the secret is not shown in repr
