"""Find out whether a PDF has a real text layer, a scan, or a mixture.

Why this matters: searching papers by topic needs their text. A PDF made from a document
has selectable text and can be read directly. A scanned or photographed PDF is only
pictures and would need OCR (reading text from images), which is slower and less exact.
This module measures which kind a PDF is, using the amount of text found per page.
"""

import io
import logging
from dataclasses import dataclass
from typing import Any

from unidex.exceptions import ConfigError

logger = logging.getLogger(__name__)

KIND_TEXT = "text"
KIND_MIXED = "mixed"
KIND_SCANNED = "scanned"
KIND_ERROR = "error"
KIND_ENCRYPTED = "encrypted"

# A page "has text" if it yields at least this many non-space characters.
PAGE_TEXT_MIN_CHARS = 80
# Share of pages with text needed to call a PDF text (below LOW it is scanned).
TEXT_SHARE = 0.8
SCANNED_SHARE = 0.2
MAX_PAGES_READ = 30
SNIPPET_CHARS = 120


@dataclass(frozen=True, slots=True)
class PdfProbe:
    """What a PDF looks like inside.

    Attributes:
        kind: ``text``, ``mixed``, ``scanned``, ``encrypted`` or ``error``.
        pages: Number of pages in the file.
        pages_read: Pages inspected (long files are cut off).
        pages_with_text: Inspected pages that yielded real text.
        chars: Non-space characters found in the inspected pages.
        snippet: The first words of text found, or empty.
    """

    kind: str
    pages: int = 0
    pages_read: int = 0
    pages_with_text: int = 0
    chars: int = 0
    snippet: str = ""

    @property
    def chars_per_page(self) -> float:
        """Average non-space characters per inspected page."""
        return self.chars / self.pages_read if self.pages_read else 0.0


def classify(pages_read: int, pages_with_text: int) -> str:
    """Decide the kind of a PDF from how many of its pages have text.

    Args:
        pages_read: Pages inspected.
        pages_with_text: Of those, pages that yielded real text.

    Returns:
        ``text``, ``mixed`` or ``scanned``.
    """
    if pages_read == 0:
        return KIND_SCANNED
    share = pages_with_text / pages_read
    if share >= TEXT_SHARE:
        return KIND_TEXT
    if share <= SCANNED_SHARE:
        return KIND_SCANNED
    return KIND_MIXED


def _reader_class() -> Any:
    """Import pypdf only when needed, with a friendly message if it is missing."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover (depends on the environment)
        raise ConfigError('Install the content extras first: pip install -e ".[content]"') from exc
    return PdfReader


def probe_pdf(data: bytes) -> PdfProbe:
    """Inspect a PDF held in memory.

    Args:
        data: The whole file.

    Returns:
        What was found. Problems are reported as ``error`` or ``encrypted`` results rather
        than exceptions, so one bad file never stops a batch.

    Raises:
        ConfigError: If the ``pypdf`` package is not installed.
    """
    reader_class = _reader_class()
    try:
        reader = reader_class(io.BytesIO(data))
        if reader.is_encrypted:
            return PdfProbe(kind=KIND_ENCRYPTED)
        total = len(reader.pages)
        read = min(total, MAX_PAGES_READ)
        with_text = 0
        chars = 0
        first_text = ""
        for index in range(read):
            text = reader.pages[index].extract_text() or ""
            count = sum(1 for ch in text if not ch.isspace())
            chars += count
            if count >= PAGE_TEXT_MIN_CHARS:
                with_text += 1
                if not first_text:
                    first_text = " ".join(text.split())[:SNIPPET_CHARS]
    except Exception as exc:
        logger.debug("Could not read PDF: %s", exc)
        return PdfProbe(kind=KIND_ERROR)
    return PdfProbe(
        kind=classify(read, with_text),
        pages=total,
        pages_read=read,
        pages_with_text=with_text,
        chars=chars,
        snippet=first_text,
    )
