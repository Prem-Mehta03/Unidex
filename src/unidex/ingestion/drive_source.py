"""A :class:`FileSource` that lists a Drive folder tree through the Drive API.

The sync job only sees :class:`FileSource`, so swapping the CSV export for this
class changes nothing else. Paths are built like the CSV export's: ``/``-joined
folder names *below* the chosen root folder (so a file directly inside the root
has the path ``/``).
"""

import logging
import re
import time
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import httpx2 as httpx

from unidex.exceptions import IngestionError
from unidex.ingestion.file_source import FileSource
from unidex.ingestion.parsing import file_extension, is_indexable
from unidex.models.raw_file import RawFile

logger = logging.getLogger(__name__)

FILES_URL = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
OPEN_URL = "https://drive.google.com/open?id="
PAGE_SIZE = 1000
FIELDS = (
    "nextPageToken,files(id,name,mimeType,modifiedTime,webViewLink,"
    "shortcutDetails(targetId,targetMimeType))"
)
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
RATE_LIMIT_REASONS = frozenset({"userRateLimitExceeded", "rateLimitExceeded", "backendError"})
MAX_ATTEMPTS = 5
MAX_FOLDERS = 20_000
REQUEST_TIMEOUT_SECONDS = 30.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def folder_id_from(text: str) -> str:
    """Accept a folder id or a folder link and return the id.

    Args:
        text: Either the id itself or an address like
            ``https://drive.google.com/drive/folders/<id>?usp=sharing``.

    Returns:
        The folder id.

    Raises:
        IngestionError: If no valid id can be found.
    """
    match = re.search(r"/folders/([A-Za-z0-9_-]+)", text)
    candidate = match.group(1) if match else text.strip()
    if not _ID_PATTERN.match(candidate):
        raise IngestionError(f"Not a Drive folder id or link: {text!r}")
    return candidate


class DriveFileSource(FileSource):
    """Walks a Drive folder and everything below it."""

    def __init__(
        self,
        token: Callable[[], str],
        root_folder: str,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        path_prefix: str = "",
    ) -> None:
        """Create the source.

        Args:
            token: Returns a valid access token each time it is called.
            root_folder: Drive folder id (or link) to list.
            http: HTTP client; tests pass one with a fake transport.
            sleep: Waits between retries; tests pass a no-op.
            path_prefix: Folder names to put in front of every path, for when the chosen
                root is itself a course folder (``"/M3"``), so paths read like the CSV
                export's (``/M3/Sem 1 .../file.pdf``) and the folder name still tells the
                extractor which course a file belongs to.
        """
        self._token = token
        self._root = folder_id_from(root_folder)
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
        self._sleep = sleep
        cleaned = "/".join(part for part in path_prefix.split("/") if part.strip())
        self._prefix = f"/{cleaned}" if cleaned else ""

    def fetch(self) -> Iterator[RawFile]:
        """Yield every file below the root folder.

        Yields:
            One :class:`RawFile` per file. Shortcuts to folders are followed (each folder
            at most once, so loops end); shortcuts to files are listed as that file.

        Raises:
            IngestionError: If Drive cannot be read completely. Nothing is yielded
                "quietly partial": the caller stores nothing from a failed run.
        """
        pending: list[tuple[str, tuple[str, ...]]] = [(self._root, ())]
        visited: set[str] = set()
        seen_files: set[str] = set()
        while pending:
            folder_id, parts = pending.pop()
            if folder_id in visited:
                continue
            visited.add(folder_id)
            if len(visited) > MAX_FOLDERS:
                raise IngestionError(f"More than {MAX_FOLDERS} folders; refusing to continue")
            if len(visited) % 25 == 0:
                logger.info("Listed %d folders so far", len(visited))
            path = self._prefix + "/" + "/".join(parts)
            path = path.rstrip("/") if self._prefix and not parts else path
            for item in sorted(self._children(folder_id), key=lambda i: str(i.get("name", ""))):
                kind = self._classify(item)
                if kind is None:
                    continue
                target_id, mime, name = kind
                if mime == FOLDER_MIME:
                    pending.append((target_id, (*parts, name)))
                elif target_id not in seen_files:
                    seen_files.add(target_id)
                    yield self._to_raw_file(item, target_id, mime, name, path)
        logger.info("Drive listing finished: %d folders, %d files", len(visited), len(seen_files))

    @staticmethod
    def _classify(item: dict[str, Any]) -> tuple[str, str, str] | None:
        """Return ``(id, mime type, name)`` of what an item really is, or ``None`` to skip."""
        name = str(item.get("name", ""))  # kept verbatim: the CSV export keeps "Midsem " too
        item_id = str(item.get("id", ""))
        mime = str(item.get("mimeType", ""))
        if not item_id or not name.strip():
            return None
        if mime == SHORTCUT_MIME:
            details = item.get("shortcutDetails") or {}
            target = str(details.get("targetId", ""))
            if not target:
                return None
            return target, str(details.get("targetMimeType", "")), name
        return item_id, mime, name

    @staticmethod
    def _to_raw_file(
        item: dict[str, Any], file_id: str, mime: str, name: str, path: str
    ) -> RawFile:
        """Convert one Drive item into a :class:`RawFile`."""
        name = name.strip()
        extension = file_extension(name)
        modified = str(item.get("modifiedTime", ""))[:10]
        try:
            modified_on: date | None = date.fromisoformat(modified) if modified else None
        except ValueError:
            modified_on = None
        is_shortcut = item.get("mimeType") == SHORTCUT_MIME
        link = None if is_shortcut else item.get("webViewLink")
        return RawFile(
            drive_file_id=file_id,
            path=path,
            name=name,
            extension=extension,
            mime_type=mime,
            modified_on=modified_on,
            url=str(link) if link else OPEN_URL + file_id,
            is_indexable=is_indexable(extension, mime, path),
        )

    def _children(self, folder_id: str) -> Iterator[dict[str, Any]]:
        """Yield every item directly inside a folder, following result pages."""
        page_token: str | None = None
        while True:
            params: dict[str, str | int] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": FIELDS,
                "pageSize": PAGE_SIZE,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            body = self._get(params, is_root=folder_id == self._root)
            yield from body.get("files", [])
            page_token = body.get("nextPageToken")
            if not page_token:
                return

    def _get(self, params: dict[str, str | int], *, is_root: bool) -> dict[str, Any]:
        """Call the Drive API, retrying temporary failures with growing pauses."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._http.get(
                    FILES_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {self._token()}"},
                )
            except httpx.HTTPError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise IngestionError("Could not reach Google Drive") from exc
                self._sleep(2 ** (attempt - 1))
                continue
            if response.status_code == httpx.codes.OK:
                try:
                    body = response.json()
                except ValueError as exc:
                    raise IngestionError("Drive sent an unreadable answer") from exc
                if isinstance(body, dict):
                    return body
                raise IngestionError("Drive sent an unexpected answer")
            if self._should_retry(response) and attempt < MAX_ATTEMPTS:
                self._sleep(2 ** (attempt - 1))
                continue
            raise IngestionError(self._explain(response, is_root))
        raise IngestionError("Drive did not answer")  # pragma: no cover (loop always returns)

    @staticmethod
    def _reason(response: httpx.Response) -> str:
        try:
            errors = response.json()["error"]["errors"]
            return str(errors[0].get("reason", ""))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            return ""

    def _should_retry(self, response: httpx.Response) -> bool:
        if response.status_code in RETRY_STATUSES:
            return True
        return response.status_code == httpx.codes.FORBIDDEN and (
            self._reason(response) in RATE_LIMIT_REASONS
        )

    def _explain(self, response: httpx.Response, is_root: bool) -> str:
        status = response.status_code
        if status == httpx.codes.NOT_FOUND and is_root:
            return (
                "Drive says that folder does not exist or is not shared with the account you "
                "signed in with. Check the folder id and run scripts/drive_login.py again."
            )
        if status == httpx.codes.UNAUTHORIZED:
            return "Drive refused the sign-in. Run python scripts/drive_login.py again."
        reason = self._reason(response)
        return f"Drive answered HTTP {status}" + (f" ({reason})" if reason else "")
