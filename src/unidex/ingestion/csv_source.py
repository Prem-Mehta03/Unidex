"""A :class:`FileSource` that reads the CSV exported by the Drive Apps Script."""

import csv
import logging
from collections.abc import Iterator
from pathlib import Path

from unidex.exceptions import IngestionError
from unidex.ingestion.file_source import FileSource
from unidex.ingestion.parsing import (
    extract_drive_file_id,
    file_extension,
    is_indexable,
    parse_modified_date,
)
from unidex.models.raw_file import RawFile

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("path", "name", "mime_type", "modified", "url")


class CsvFileSource(FileSource):
    """Reads a CSV with the columns ``path, name, mime_type, modified, url``."""

    def __init__(self, csv_path: Path) -> None:
        """Remember where the CSV is.

        Args:
            csv_path: Path to the CSV file.
        """
        self._csv_path = csv_path

    def fetch(self) -> Iterator[RawFile]:
        """Yield one :class:`RawFile` per valid CSV row.

        Rows that cannot be parsed (for example, a link without a Drive id) are
        skipped with a warning instead of stopping the whole import.

        Yields:
            One :class:`RawFile` per valid row.

        Raises:
            IngestionError: If the file is missing or lacks a required column.
        """
        if not self._csv_path.is_file():
            raise IngestionError(f"CSV file not found: {self._csv_path}")

        skipped = 0
        with self._csv_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
            if missing:
                raise IngestionError(f"{self._csv_path} is missing columns: {', '.join(missing)}")
            for line_number, row in enumerate(reader, start=2):
                try:
                    yield self._to_raw_file(row)
                except IngestionError as exc:
                    skipped += 1
                    logger.warning("Skipping CSV line %d: %s", line_number, exc)
        if skipped:
            logger.warning("Skipped %d unreadable rows in %s", skipped, self._csv_path)

    @staticmethod
    def _to_raw_file(row: dict[str, str]) -> RawFile:
        """Convert one CSV row into a :class:`RawFile`.

        Args:
            row: A row from ``csv.DictReader``.

        Returns:
            The parsed file.

        Raises:
            IngestionError: If the URL or date in the row is invalid.
        """
        name = row["name"].strip()
        extension = file_extension(name)
        mime_type = row["mime_type"].strip()
        return RawFile(
            drive_file_id=extract_drive_file_id(row["url"]),
            path=row["path"].strip(),
            name=name,
            extension=extension,
            mime_type=mime_type,
            modified_on=parse_modified_date(row["modified"]),
            url=row["url"].strip(),
            is_indexable=is_indexable(extension, mime_type),
        )
