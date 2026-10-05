import io
import shutil

import pytest
from PIL import Image
from pypdf import PdfWriter

from tests.unit._pdf_data import LONG, make_pdf
from unidex.content.quality import MIN_QUALITY, text_quality
from unidex.content.readers import (
    ENGINE_NONE,
    OcrReader,
    PdfTextReader,
    RapidOcrEngine,
    TesseractEngine,
    UnreadablePdfError,
    build_engine,
    non_space_chars,
)
from unidex.exceptions import ConfigError


class TestQuality:
    @pytest.mark.parametrize(
        "text",
        [
            LONG,
            "BITS Pilani K K Birla Goa Campus Course Title: Mathematics III Quiz-2 Max. Marks: 75",
            "Question 1 Correct Mark 4.00 out of 4.00 Create a RectanglePoint class",
            "CS F222 Tutorial - 8 Problem 10.1.1 : Draw graph models, stating the type of graph",
        ],
    )
    def test_clean_text_scores_high(self, text: str) -> None:
        assert text_quality(text) >= 0.8

    @pytest.mark.parametrize(
        "text",
        [
            "Q1)ProofbyStructuralInduction Base Step: A=pelP length/A)=1 isodd (Mark",
            "Lecture27(oct23,2024) (U,v) , (w,V') ii)enso #U-wandv-v iv)Arong- (U, V) (4,VI)",
            "Valuation,Assignment v:P→{T,F} = V:IF→{T,F} extensionof✓ interpretation",
            "Betofsets:- ① 53 ↑ &A= & 1, 2,41,23, [1,2, 3,03 -Ianelementwhich is aset",
        ],
    )
    def test_garbled_handwriting_scores_low(self, text: str) -> None:
        assert text_quality(text) < MIN_QUALITY

    def test_empty_and_whitespace(self) -> None:
        assert text_quality("") == 0.0
        assert text_quality("   \n\t ") == 0.0

    def test_numbers_and_punctuation_count_as_readable(self) -> None:
        assert text_quality("12/21/21 4.00 (3) 2018-19") == 1.0


def test_non_space_chars() -> None:
    assert non_space_chars(" a b\nc ") == 3


class TestPdfTextReader:
    def test_reads_page_by_page(self) -> None:
        result = PdfTextReader().read(make_pdf([LONG, "", LONG]), max_pages=10)
        assert result.total_pages == 3
        assert [bool(p.strip()) for p in result.pages] == [True, False, True]

    def test_page_limit(self) -> None:
        result = PdfTextReader().read(make_pdf([LONG] * 5), max_pages=2)
        assert (len(result.pages), result.total_pages) == (2, 5)

    def test_encrypted_and_broken_files_are_unreadable(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        writer.encrypt("pw")
        buffer = io.BytesIO()
        writer.write(buffer)
        for data in (buffer.getvalue(), b"junk"):
            with pytest.raises(UnreadablePdfError):
                PdfTextReader().read(data, 5)


def scanned_pdf() -> bytes:
    """A PDF whose only content is a picture of a page of text (what a scan is)."""
    import pypdfium2

    page_image = pypdfium2.PdfDocument(make_pdf([LONG])).get_page(0).render(scale=200 / 72)
    buffer = io.BytesIO()
    page_image.to_pil().convert("RGB").save(buffer, "PDF", resolution=200)
    return buffer.getvalue()


def test_a_scan_has_no_text_layer() -> None:
    result = PdfTextReader().read(scanned_pdf(), 5)
    assert result.total_pages == 1
    assert non_space_chars(result.pages[0]) == 0


class FakeEngine:
    def __init__(self) -> None:
        self.images: list[Image.Image] = []

    def recognize(self, image: Image.Image) -> str:
        self.images.append(image)
        return f"page of size {image.width}"


def test_ocr_reader_renders_requested_pages_only() -> None:
    engine = FakeEngine()
    texts = OcrReader(engine, dpi=100).read_pages(make_pdf([LONG, LONG, LONG]), [0, 2])
    assert sorted(texts) == [0, 2]
    assert len(engine.images) == 2
    assert engine.images[0].width == round(612 * 100 / 72)


class TestEngines:
    def test_none_means_no_engine(self) -> None:
        assert build_engine(ENGINE_NONE) is None

    @pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract is not installed")
    def test_tesseract_reads_a_scanned_page(self) -> None:
        texts = OcrReader(TesseractEngine()).read_pages(scanned_pdf(), [0])
        assert "inheritance" in texts[0].lower()
        assert "polymorphism" in texts[0].lower()

    def test_rapidocr_reads_a_scanned_page(self) -> None:
        pytest.importorskip("rapidocr")
        texts = OcrReader(RapidOcrEngine()).read_pages(scanned_pdf(), [0])
        assert "inheritance" in texts[0].lower()

    def test_auto_picks_something_or_nothing_without_failing(self) -> None:
        engine = build_engine("auto")
        assert engine is None or hasattr(engine, "recognize")

    def test_missing_tesseract_program_is_explained(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(ConfigError, match="rapidocr"):
            TesseractEngine()
