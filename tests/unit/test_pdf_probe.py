import io

import httpx2 as httpx
import pytest
from pypdf import PdfWriter

from tests.unit._pdf_data import LONG, make_pdf
from unidex.content.probe import (
    KIND_ENCRYPTED,
    KIND_ERROR,
    KIND_MIXED,
    KIND_SCANNED,
    KIND_TEXT,
    classify,
    probe_pdf,
)
from unidex.exceptions import IngestionError
from unidex.ingestion.drive_download import DriveDownloader


class TestClassify:
    @pytest.mark.parametrize(
        ("read", "with_text", "kind"),
        [
            (0, 0, KIND_SCANNED),
            (10, 10, KIND_TEXT),
            (10, 8, KIND_TEXT),
            (10, 5, KIND_MIXED),
            (10, 2, KIND_SCANNED),
            (10, 0, KIND_SCANNED),
        ],
    )
    def test_kinds(self, read: int, with_text: int, kind: str) -> None:
        assert classify(read, with_text) == kind


class TestProbe:
    def test_text_pdf(self) -> None:
        result = probe_pdf(make_pdf([LONG, LONG]))
        assert result.kind == KIND_TEXT
        assert result.pages == 2
        assert result.pages_with_text == 2
        assert result.chars_per_page > 80
        assert result.snippet.startswith("Question one")

    def test_pdf_without_text_is_scanned(self) -> None:
        result = probe_pdf(make_pdf(["", ""]))
        assert result.kind == KIND_SCANNED
        assert result.chars == 0
        assert result.snippet == ""

    def test_half_and_half_is_mixed(self) -> None:
        assert probe_pdf(make_pdf([LONG, "", LONG, ""])).kind == KIND_MIXED

    def test_blank_page_from_pypdf(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        buffer = io.BytesIO()
        writer.write(buffer)
        assert probe_pdf(buffer.getvalue()).kind == KIND_SCANNED

    def test_encrypted_pdf(self) -> None:
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        writer.encrypt("secret")
        buffer = io.BytesIO()
        writer.write(buffer)
        assert probe_pdf(buffer.getvalue()).kind == KIND_ENCRYPTED

    @pytest.mark.parametrize("data", [b"", b"not a pdf at all", b"%PDF-1.4 broken"])
    def test_garbage_is_an_error_result_not_an_exception(self, data: bytes) -> None:
        assert probe_pdf(data).kind == KIND_ERROR

    def test_only_the_first_pages_are_read(self) -> None:
        result = probe_pdf(make_pdf([LONG] * 35))
        assert result.pages == 35
        assert result.pages_read == 30


def downloader(handler, sleeps: list[float] | None = None) -> DriveDownloader:
    return DriveDownloader(
        lambda: "tok",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=(sleeps if sleeps is not None else []).append,
    )


class TestDownloader:
    def test_downloads_with_token_and_media_flag(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers["authorization"]
            seen["params"] = dict(request.url.params)
            seen["path"] = request.url.path
            return httpx.Response(200, content=b"PDFBYTES")

        assert downloader(handler).download("FILE1") == b"PDFBYTES"
        assert seen["auth"] == "Bearer tok"
        assert seen["params"]["alt"] == "media"
        assert seen["path"].endswith("/files/FILE1")

    def test_retries_temporary_errors(self) -> None:
        answers = iter(
            [httpx.Response(503), httpx.Response(429), httpx.Response(200, content=b"x")]
        )
        sleeps: list[float] = []
        assert downloader(lambda r: next(answers), sleeps).download("F") == b"x"
        assert sleeps == [1, 2]

    def test_gives_up_on_permission_errors_immediately(self) -> None:
        sleeps: list[float] = []
        with pytest.raises(IngestionError, match="403"):
            downloader(lambda r: httpx.Response(403), sleeps).download("F")
        assert sleeps == []

    def test_too_large(self) -> None:
        with pytest.raises(IngestionError, match="larger"):
            downloader(lambda r: httpx.Response(200, content=b"x" * 100)).download(
                "F", max_bytes=10
            )

    def test_network_failure(self) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(IngestionError, match="Could not download"):
            downloader(boom).download("F")
