import json
import urllib.request

import pytest

from unidex.exceptions import ConfigError, LLMError, LLMRateLimitError
from unidex.extraction.llm_client import GeminiClient, HttpResponse, MockLLMClient

OK_BODY = json.dumps({"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}}]})


class FakeTransport:
    """Records the request and returns a prepared response."""

    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request, timeout: float) -> HttpResponse:
        self.requests.append(request)
        return self.response


def make_client(response: HttpResponse) -> tuple[GeminiClient, FakeTransport]:
    transport = FakeTransport(response)
    return GeminiClient("SECRET-KEY", "gemini-test-1", transport=transport), transport


def test_successful_call_returns_the_reply_text() -> None:
    client, transport = make_client(HttpResponse(200, OK_BODY))
    assert client.complete_json("hello") == "[1, 2]"
    sent = json.loads(transport.requests[0].data)  # type: ignore[arg-type]
    assert sent["contents"][0]["parts"][0]["text"] == "hello"
    assert sent["generationConfig"]["responseMimeType"] == "application/json"
    assert sent["generationConfig"]["temperature"] == 0


def test_key_is_sent_in_a_header_never_in_the_url() -> None:
    client, transport = make_client(HttpResponse(200, OK_BODY))
    client.complete_json("hello")
    request = transport.requests[0]
    assert "SECRET-KEY" not in request.full_url
    assert request.get_header("X-goog-api-key") == "SECRET-KEY"
    assert request.full_url.endswith("/models/gemini-test-1:generateContent")


def test_http_429_is_a_rate_limit_error() -> None:
    client, _ = make_client(HttpResponse(429, "quota"))
    with pytest.raises(LLMRateLimitError):
        client.complete_json("x")


def test_other_http_errors_raise_without_leaking_the_key() -> None:
    client, _ = make_client(HttpResponse(500, "server broke"))
    with pytest.raises(LLMError) as info:
        client.complete_json("x")
    assert "500" in str(info.value)
    assert "SECRET-KEY" not in str(info.value)


@pytest.mark.parametrize("body", ["not json", "{}", '{"candidates": []}', '{"candidates": [{}]}'])
def test_unexpected_reply_shapes_raise(body: str) -> None:
    client, _ = make_client(HttpResponse(200, body))
    with pytest.raises(LLMError):
        client.complete_json("x")


@pytest.mark.parametrize(
    ("key", "model"),
    [(None, "m"), ("", "m"), ("k", None), ("k", ""), ("k", "bad/model?x=1"), ("k", "a b")],
)
def test_bad_configuration_is_rejected_up_front(key: str | None, model: str | None) -> None:
    with pytest.raises(ConfigError):
        GeminiClient(key, model)


def test_key_is_not_shown_in_the_clients_repr() -> None:
    client, _ = make_client(HttpResponse(200, OK_BODY))
    assert "SECRET-KEY" not in repr(client)


def test_mock_client_repeats_its_last_reply() -> None:
    mock = MockLLMClient(replies=["a", "b"])
    assert [mock.complete_json("p") for _ in range(3)] == ["a", "b", "b"]
