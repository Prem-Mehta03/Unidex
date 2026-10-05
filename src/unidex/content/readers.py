"""Ways of getting text out of a PDF: its text layer, or OCR on page pictures.

OCR (optical character recognition) means looking at a picture of a page and typing out the
words, like a very fast reader. Two interchangeable engines are supported (the Strategy
pattern): Tesseract (needs a separate program installed) and RapidOCR (a plain ``pip install``).
"""

import io
import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from unidex.exceptions import ConfigError

logger = logging.getLogger(__name__)

PAGE_TEXT_MIN_CHARS = 80
OCR_DPI = 200
PDF_POINTS_PER_INCH = 72

ENGINE_NONE = "none"
ENGINE_AUTO = "auto"
ENGINE_TESSERACT = "tesseract"
ENGINE_RAPIDOCR = "rapidocr"
ENGINE_CHOICES = (ENGINE_NONE, ENGINE_AUTO, ENGINE_TESSERACT, ENGINE_RAPIDOCR)


@dataclass(frozen=True, slots=True)
class PdfPages:
    """The text layer of a PDF, page by page.

    Attributes:
        pages: Text of each inspected page (empty string for a page with no text).
        total_pages: Pages in the whole file (may be more than were inspected).
    """

    pages: list[str]
    total_pages: int


class UnreadablePdfError(Exception):
    """The file is encrypted or not a readable PDF (a permanent condition, not worth retrying)."""


def non_space_chars(text: str) -> int:
    """Count the characters that are not whitespace."""
    return sum(1 for ch in text if not ch.isspace())


class PdfTextReader:
    """Reads the text layer with pypdf."""

    def read(self, data: bytes, max_pages: int) -> PdfPages:
        """Read the first pages of a PDF.

        Args:
            data: The whole file.
            max_pages: Read at most this many pages.

        Returns:
            Text per page.

        Raises:
            ConfigError: If ``pypdf`` is not installed.
            UnreadablePdfError: If the file is encrypted or broken.
        """
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover (depends on the environment)
            raise ConfigError('Install the extras: pip install -e ".[content]"') from exc
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise UnreadablePdfError("encrypted")
            total = len(reader.pages)
            pages = [(reader.pages[i].extract_text() or "") for i in range(min(total, max_pages))]
        except UnreadablePdfError:
            raise
        except Exception as exc:
            raise UnreadablePdfError(str(exc)) from exc
        return PdfPages(pages=pages, total_pages=total)


class OcrEngine(Protocol):
    """Turns a page picture into text."""

    def recognize(self, image: Any) -> str:
        """Read the text in a PIL image."""
        ...


class TesseractEngine:
    """OCR with the Tesseract program (install it separately and put it on the PATH)."""

    def __init__(self) -> None:
        """Check that pytesseract and the Tesseract program are available.

        Raises:
            ConfigError: If either is missing.
        """
        try:
            import pytesseract
        except ImportError as exc:
            raise ConfigError('Install the OCR extras: pip install -e ".[ocr]"') from exc
        if shutil.which("tesseract") is None:
            raise ConfigError(
                "The Tesseract program is not installed or not on the PATH. "
                "Use --ocr rapidocr instead (no extra program needed)."
            )
        self._pytesseract = pytesseract

    def recognize(self, image: Any) -> str:
        """Read the text in a PIL image."""
        return str(self._pytesseract.image_to_string(image, lang="eng"))


class RapidOcrEngine:
    """OCR with RapidOCR (pure ``pip install``; downloads nothing at run time)."""

    def __init__(self) -> None:
        """Load the model.

        Raises:
            ConfigError: If rapidocr is not installed.
        """
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise ConfigError('Install the OCR extras: pip install -e ".[ocr]"') from exc
        self._engine = RapidOCR()

    def recognize(self, image: Any) -> str:
        """Read the text in a PIL image."""
        import numpy as np

        result = self._engine(np.array(image))
        texts = getattr(result, "txts", None)
        if not texts:
            return ""
        return "\n".join(str(line) for line in texts)


def build_engine(name: str) -> OcrEngine | None:
    """Create the OCR engine asked for.

    Args:
        name: One of :data:`ENGINE_CHOICES`. ``auto`` picks Tesseract if it is installed,
            otherwise RapidOCR if that is installed, otherwise no OCR.

    Returns:
        The engine, or ``None`` for no OCR.

    Raises:
        ConfigError: If a named engine cannot be used.
    """
    if name == ENGINE_NONE:
        return None
    if name == ENGINE_TESSERACT:
        return TesseractEngine()
    if name == ENGINE_RAPIDOCR:
        return RapidOcrEngine()
    for candidate in (TesseractEngine, RapidOcrEngine):
        try:
            return candidate()
        except ConfigError:
            continue
    logger.warning("No OCR engine is available; scanned pages will be skipped")
    return None


class OcrReader:
    """Renders PDF pages to pictures and reads them with an engine."""

    def __init__(self, engine: OcrEngine, dpi: int = OCR_DPI) -> None:
        """Create the reader.

        Args:
            engine: The OCR engine.
            dpi: Picture sharpness; 200 is a good balance of accuracy and speed.
        """
        self._engine = engine
        self._scale = dpi / PDF_POINTS_PER_INCH

    def read_pages(self, data: bytes, page_numbers: Iterable[int]) -> dict[int, str]:
        """OCR some pages of a PDF.

        Args:
            data: The whole file.
            page_numbers: Zero-based page numbers to read.

        Returns:
            ``{page number: text}``.

        Raises:
            ConfigError: If pypdfium2 is not installed.
        """
        try:
            import pypdfium2
        except ImportError as exc:
            raise ConfigError('Install the OCR extras: pip install -e ".[ocr]"') from exc
        document = pypdfium2.PdfDocument(data)
        texts: dict[int, str] = {}
        try:
            for number in page_numbers:
                image = document[number].render(scale=self._scale).to_pil()
                texts[number] = self._engine.recognize(image)
        finally:
            document.close()
        return texts
