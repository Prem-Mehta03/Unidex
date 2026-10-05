"""Download file bytes from Drive (read-only), for reading what is inside files."""

import logging
import time
from collections.abc import Callable

import httpx2 as httpx

from unidex.exceptions import IngestionError

logger = logging.getLogger(__name__)

FILES_URL = "https://www.googleapis.com/drive/v3/files"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


class DriveDownloader:
    """Fetches one file's content at a time."""

    def __init__(
        self,
        token: Callable[[], str],
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Create the downloader.

        Args:
            token: Returns a valid access token each time it is called.
            http: HTTP client; tests pass one with a fake transport.
            sleep: Waits between retries; tests pass a no-op.
        """
        self._token = token
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._sleep = sleep

    def download(self, file_id: str, max_bytes: int = DEFAULT_MAX_BYTES) -> bytes:
        """Download a file.

        Args:
            file_id: Drive file id.
            max_bytes: Refuse files larger than this.

        Returns:
            The file's bytes.

        Raises:
            IngestionError: If the file cannot be fetched or is too large.
        """
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._http.get(
                    f"{FILES_URL}/{file_id}",
                    params={"alt": "media", "supportsAllDrives": "true"},
                    headers={"Authorization": f"Bearer {self._token()}"},
                )
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise IngestionError(f"Could not download {file_id}") from exc
                self._sleep(2 ** (attempt - 1))
                continue
            if response.status_code == httpx.codes.OK:
                if len(response.content) > max_bytes:
                    raise IngestionError(f"{file_id} is larger than {max_bytes} bytes")
                return response.content
            if response.status_code in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                self._sleep(2 ** (attempt - 1))
                continue
            raise IngestionError(f"Drive answered HTTP {response.status_code} for {file_id}")
        raise IngestionError(f"Could not download {file_id}")  # pragma: no cover
