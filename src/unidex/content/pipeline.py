"""Read the text inside documents and store it for topic search.

For each PDF document without stored text: download it (read-only), read its text layer,
OCR the pages that have none (if an engine is available), judge the quality, and store the
result. Rows are saved one at a time, so a stopped run simply continues next time.
"""

import logging
import sqlite3
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from unidex.content.quality import MIN_QUALITY, text_quality
from unidex.content.readers import (
    PAGE_TEXT_MIN_CHARS,
    OcrReader,
    PdfTextReader,
    UnreadablePdfError,
    non_space_chars,
)
from unidex.db.repositories import DocumentTextRepository, ReadTask
from unidex.exceptions import IngestionError

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 40
MAX_OCR_PAGES = 12
MAX_TEXT_CHARS = 60_000
MIN_COVERAGE = 0.5

METHOD_TEXT = "pdf_text"
METHOD_OCR = "ocr"
METHOD_NONE = "none"


class Downloader(Protocol):
    """Fetches a file's bytes."""

    def download(self, file_id: str) -> bytes:
        """Return the content of a Drive file."""
        ...


@dataclass(frozen=True, slots=True)
class ContentReport:
    """What a run did.

    Attributes:
        processed: Documents handled this run.
        from_text: Stored with text from the PDF's text layer.
        from_ocr: Stored with text that needed OCR for some pages.
        unreadable: Stored as having no usable text (scans, handwriting, encrypted).
        failed: Could not be downloaded; they are tried again next run.
    """

    processed: int
    from_text: int
    from_ocr: int
    unreadable: int
    failed: int


class ContentPipeline:
    """Reads documents' contents and stores the text."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        downloader: Downloader,
        *,
        ocr: OcrReader | None = None,
        text_reader: PdfTextReader | None = None,
        min_quality: float = MIN_QUALITY,
        max_pages: int = DEFAULT_MAX_PAGES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Configure the pipeline.

        Args:
            conn: An open connection with the schema created.
            downloader: Fetches file bytes from Drive.
            ocr: Reads pages that have no text layer; ``None`` skips them.
            text_reader: Reads text layers (the default uses pypdf).
            min_quality: Text scoring below this is stored as unreadable.
            max_pages: Read at most this many pages of each file.
            clock: Returns the current UTC time; tests pass a fixed one.
        """
        self._repo = DocumentTextRepository(conn)
        self._downloader = downloader
        self._ocr = ocr
        self._reader = text_reader or PdfTextReader()
        self._min_quality = min_quality
        self._max_pages = max_pages
        self._clock = clock

    def run(
        self, doc_types: Collection[str], *, limit: int | None = None, force: bool = False
    ) -> ContentReport:
        """Read every pending document of the given types.

        Args:
            doc_types: Document types to read, e.g. ``("pyq", "solution")``.
            limit: Stop after this many documents (useful for a first trial run).
            force: Read again even documents that already have text.

        Returns:
            Counts of what happened.
        """
        tasks = self._repo.pending(doc_types, force=force)
        if limit is not None:
            tasks = tasks[:limit]
        logger.info("%d documents to read", len(tasks))
        counts = {METHOD_TEXT: 0, METHOD_OCR: 0, METHOD_NONE: 0, "failed": 0}
        for index, task in enumerate(tasks, start=1):
            counts[self._read_one(task)] += 1
            if index % 10 == 0:
                logger.info("  %d / %d done", index, len(tasks))
        return ContentReport(
            processed=len(tasks),
            from_text=counts[METHOD_TEXT],
            from_ocr=counts[METHOD_OCR],
            unreadable=counts[METHOD_NONE],
            failed=counts["failed"],
        )

    def _read_one(self, task: ReadTask) -> str:
        """Read and store one document; return what happened (a method name or ``failed``)."""
        now = self._clock().isoformat(timespec="seconds")
        try:
            data = self._downloader.download(task.drive_file_id)
        except IngestionError as exc:
            logger.warning("Could not download %s: %s", task.name, exc)
            return "failed"
        try:
            parsed = self._reader.read(data, self._max_pages)
        except UnreadablePdfError as exc:
            logger.info("Unreadable PDF %s: %s", task.name, exc)
            self._repo.save(task.document_id, METHOD_NONE, 0.0, 0, "", task.modified_on, now)
            return METHOD_NONE
        pages = list(parsed.pages)
        method = METHOD_TEXT
        empty = [i for i, text in enumerate(pages) if non_space_chars(text) < PAGE_TEXT_MIN_CHARS]
        if empty and self._ocr is not None:
            try:
                for number, text in self._ocr.read_pages(data, empty[:MAX_OCR_PAGES]).items():
                    pages[number] = text
                method = METHOD_OCR
            except Exception:
                logger.warning("OCR failed for %s", task.name, exc_info=True)
        with_text = sum(1 for text in pages if non_space_chars(text) >= PAGE_TEXT_MIN_CHARS)
        text = "\n".join(pages)[:MAX_TEXT_CHARS]
        coverage = with_text / len(pages) if pages else 0.0
        quality = text_quality(text)
        if coverage < MIN_COVERAGE or quality < self._min_quality:
            self._repo.save(
                task.document_id,
                METHOD_NONE,
                quality,
                parsed.total_pages,
                "",
                task.modified_on,
                now,
            )
            return METHOD_NONE
        self._repo.save(
            task.document_id, method, quality, parsed.total_pages, text, task.modified_on, now
        )
        return method
