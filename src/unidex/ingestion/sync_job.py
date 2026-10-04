"""The sync job: copy a drive listing into the ``raw_files`` table."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from unidex.db.connection import transaction
from unidex.db.repositories import DepartmentRepository, RawFileRepository, SourceRepository
from unidex.ingestion.file_source import FileSource

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """What a sync run did.

    Attributes:
        seen: Files found in the listing.
        inserted: Files that were new.
        updated: Files that were already known and got refreshed.
        indexable: Files in the listing flagged as searchable study material.
    """

    seen: int
    inserted: int
    updated: int
    indexable: int


class SyncJob:
    """Copies one source's listing into the database, atomically."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        source: FileSource,
        department_name: str,
        source_label: str,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Configure a sync run.

        Args:
            conn: An open connection with the schema created.
            source: Where the file listing comes from.
            department_name: Department that owns the drive, e.g. ``"CS"``.
            source_label: Unique label for this drive, e.g. ``"CS archive"``.
            clock: Returns the current time; replaced in tests for determinism.
        """
        self._conn = conn
        self._source = source
        self._department_name = department_name
        self._source_label = source_label
        self._clock = clock

    def run(self) -> SyncResult:
        """Fetch the listing and store it.

        The listing is read completely *before* the database transaction
        opens, so a slow or failing source never holds a transaction open. All
        writes then happen in one transaction: if anything fails, nothing is
        stored.

        Returns:
            Counts describing the run.

        Raises:
            UnidexError: If the source cannot be read.
        """
        files = list(self._source.fetch())
        now = self._clock().isoformat(timespec="seconds")

        with transaction(self._conn):
            department_id = DepartmentRepository(self._conn).get_or_create(self._department_name)
            sources = SourceRepository(self._conn)
            source_id = sources.get_or_create(department_id, self._source_label)
            counts = RawFileRepository(self._conn).upsert_many(source_id, files, now)
            sources.mark_synced(source_id, now)

        result = SyncResult(
            seen=len(files),
            inserted=counts.inserted,
            updated=counts.updated,
            indexable=sum(1 for f in files if f.is_indexable),
        )
        logger.info(
            "Sync of %r finished: %d seen, %d new, %d updated, %d indexable",
            self._source_label,
            result.seen,
            result.inserted,
            result.updated,
            result.indexable,
        )
        return result
