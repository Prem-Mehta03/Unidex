from datetime import date

import pytest

from unidex.exceptions import IngestionError
from unidex.ingestion.parsing import (
    extract_drive_file_id,
    file_extension,
    is_indexable,
    parse_modified_date,
)
from unidex.normalize import normalize_alias


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://drive.google.com/file/d/1BoK8AwC2t-GIoGlcf0Raf_nQnwOAylyW/view?usp=drivesdk",
            "1BoK8AwC2t-GIoGlcf0Raf_nQnwOAylyW",
        ),
        (
            "https://docs.google.com/document/d/13iXm5WKBqzXc4fb/edit?usp=drivesdk",
            "13iXm5WKBqzXc4fb",
        ),
        ("https://drive.google.com/drive/folders/abc123", "abc123"),
        ("https://drive.google.com/open?id=xyz-789", "xyz-789"),
    ],
)
def test_extract_drive_file_id(url: str, expected: str) -> None:
    assert extract_drive_file_id(url) == expected


def test_extract_drive_file_id_rejects_unknown_url() -> None:
    with pytest.raises(IngestionError):
        extract_drive_file_id("https://example.com/not-a-drive-link")


def test_parse_modified_date_is_month_first() -> None:
    assert parse_modified_date("12/4/2025") == date(2025, 12, 4)
    assert parse_modified_date("1/9/2026") == date(2026, 1, 9)


def test_parse_modified_date_blank_is_none() -> None:
    assert parse_modified_date("  ") is None


def test_parse_modified_date_rejects_garbage() -> None:
    with pytest.raises(IngestionError):
        parse_modified_date("13/45/2025")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Midsem QP.PDF", "pdf"),
        ("full_adder.v", "v"),
        ("archive.tar.gz", "gz"),
        ("Av Details", ""),
        (".DS_Store", ""),
    ],
)
def test_file_extension(name: str, expected: str) -> None:
    assert file_extension(name) == expected


@pytest.mark.parametrize(
    ("extension", "mime_type", "expected"),
    [
        ("pdf", "application/pdf", True),
        ("pptx", "application/octet-stream", True),
        ("png", "image/png", True),
        ("", "application/vnd.google-apps.document", True),
        ("v", "application/octet-stream", False),
        ("zip", "application/zip", False),
        ("", "application/octet-stream", False),
        ("vcd", "application/x-cdlink", False),
    ],
)
def test_is_indexable(extension: str, mime_type: str, expected: bool) -> None:
    assert is_indexable(extension, mime_type) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("CS F213", "csf213"),
        ("csf213", "csf213"),
        ("CS-F213", "csf213"),
        ("Logic in CS", "logicincs"),
        ("  OOP  ", "oop"),
        ("---", ""),
    ],
)
def test_normalize_alias(text: str, expected: str) -> None:
    assert normalize_alias(text) == expected
