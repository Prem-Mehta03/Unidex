from pathlib import Path

import pytest

from unidex.exceptions import IngestionError
from unidex.ingestion.csv_source import CsvFileSource

HEADER = "path,name,mime_type,modified,url\n"
GOOD = (
    "/2-1 CDCs/M3,Quiz 1.pdf,application/pdf,10/1/2025,https://drive.google.com/file/d/abc/view\n"
)
BAD_URL = "/2-1 CDCs/M3,Quiz 2.pdf,application/pdf,10/1/2025,https://example.com/nope\n"
BAD_DATE = (
    "/2-1 CDCs/M3,Quiz 3.pdf,application/pdf,99/99/2025,https://drive.google.com/file/d/def/view\n"
)
CODE = "/2-1 CDCs/DD,adder.v,application/octet-stream,10/1/2025,https://drive.google.com/file/d/ghi/view\n"


def write_csv(tmp_path: Path, body: str, header: str = HEADER) -> Path:
    path = tmp_path / "listing.csv"
    path.write_text(header + body, encoding="utf-8")
    return path


def test_valid_rows_become_raw_files(tmp_path: Path) -> None:
    files = list(CsvFileSource(write_csv(tmp_path, GOOD + CODE)).fetch())
    assert [f.name for f in files] == ["Quiz 1.pdf", "adder.v"]
    assert files[0].drive_file_id == "abc"
    assert files[0].extension == "pdf"
    assert files[0].is_indexable is True
    assert files[1].is_indexable is False


def test_unreadable_rows_are_skipped_not_fatal(tmp_path: Path) -> None:
    files = list(CsvFileSource(write_csv(tmp_path, BAD_URL + GOOD + BAD_DATE)).fetch())
    assert [f.name for f in files] == ["Quiz 1.pdf"]


def test_missing_column_raises(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "", header="path,name\n")
    with pytest.raises(IngestionError):
        list(CsvFileSource(path).fetch())


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(IngestionError):
        list(CsvFileSource(tmp_path / "absent.csv").fetch())
