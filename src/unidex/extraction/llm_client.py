"""Language-model clients behind one small interface.

The rest of Unidex only knows :class:`LLMClient`. Swapping Gemini for another
provider, or for a fake in tests, means writing one more subclass.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from unidex.exceptions import ConfigError, LLMError, LLMRateLimitError

logger = logging.getLogger(__name__)

GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+$")
HTTP_TOO_MANY_REQUESTS = 429
DEFAULT_TIMEOUT_SECONDS = 60.0


class LLMClient(ABC):
    """Anything that can answer a prompt with JSON text."""

    @abstractmethod
    def complete_json(self, prompt: str) -> str:
        """Send a prompt and return the model's reply text.

        Args:
            prompt: The full instruction and data to send.

        Returns:
            The reply text, expected to be JSON.

        Raises:
            LLMRateLimitError: If the provider says a quota was reached.
            LLMError: For any other failure.
        """


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """What an HTTP call returned.

    Attributes:
        status: HTTP status code.
        body: Response body text.
    """

    status: int
    body: str


Transport = Callable[[urllib.request.Request, float], HttpResponse]


def urllib_transport(request: urllib.request.Request, timeout: float) -> HttpResponse:
    """Send a request with the standard library.

    Args:
        request: The prepared request.
        timeout: Seconds to wait before giving up.

    Returns:
        The status and body, including for HTTP error statuses.

    Raises:
        LLMError: If the server cannot be reached.
    """
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return HttpResponse(response.status, response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise LLMError(f"Could not reach the Gemini API: {exc}") from exc


class GeminiClient(LLMClient):
    """Calls the Gemini REST API (free tier) over HTTPS.

    The key travels in a request header, not in the URL, so it does not end up
    in logs or proxies that record URLs. It is never logged.
    """

    def __init__(
        self,
        api_key: str | None,
        model: str | None,
        transport: Transport = urllib_transport,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Create a client.

        Args:
            api_key: Gemini API key from the environment.
            model: Model id copied from AI Studio.
            transport: Function that performs the HTTP call; replaced in tests.
            timeout: Seconds to wait for each request.

        Raises:
            ConfigError: If the key or model is missing, or the model id has
                characters that do not belong in a model id.
        """
        if not api_key:
            raise ConfigError("GEMINI_API_KEY is not set; add it to your .env file")
        if not model:
            raise ConfigError("GEMINI_MODEL is not set; copy the model id from AI Studio")
        if not _MODEL_ID.match(model):
            raise ConfigError(f"GEMINI_MODEL contains unexpected characters: {model!r}")
        self._api_key = api_key
        self._url = GEMINI_ENDPOINT.format(model=model)
        self._transport = transport
        self._timeout = timeout

    def complete_json(self, prompt: str) -> str:
        """Send a prompt to Gemini and return the reply text.

        Args:
            prompt: The full instruction and data to send.

        Returns:
            The text of the first candidate.

        Raises:
            LLMRateLimitError: On HTTP 429.
            LLMError: On other HTTP errors, blocked prompts or malformed replies.
        """
        body = json.dumps(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
            }
        ).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310
            self._url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key},
        )
        response = self._transport(request, self._timeout)
        if response.status == HTTP_TOO_MANY_REQUESTS:
            raise LLMRateLimitError("Gemini reported that a quota was reached (HTTP 429)")
        if response.status != 200:
            raise LLMError(f"Gemini returned HTTP {response.status}: {response.body[:200]}")
        return self._reply_text(response.body)

    @staticmethod
    def _reply_text(body: str) -> str:
        """Pull the reply text out of a Gemini response body."""
        try:
            data = json.loads(body)
            return str(data["candidates"][0]["content"]["parts"][0]["text"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError("Gemini reply did not have the expected shape") from exc


@dataclass(slots=True)
class MockLLMClient(LLMClient):
    """A scripted client for tests: returns prepared replies and records prompts.

    Attributes:
        replies: Replies handed out in order. When they run out the last one repeats.
        prompts: Every prompt received, oldest first.
        error: If set, raised instead of returning a reply.
    """

    replies: list[str]
    prompts: list[str] = field(default_factory=list)
    error: LLMError | None = None

    def complete_json(self, prompt: str) -> str:
        """Record the prompt and return the next prepared reply.

        Args:
            prompt: The prompt to record.

        Returns:
            The next scripted reply.

        Raises:
            LLMError: The configured ``error``, if any.
        """
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        index = min(len(self.prompts) - 1, len(self.replies) - 1)
        return self.replies[index]
