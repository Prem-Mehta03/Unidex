"""The sync job: copy a drive listing into the ``raw_files`` table."""

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from unidex.db.connection import transaction
from unidex.db.repositories import DepartmentRepository, RawFileRepository, SourceRepository
from unidex.exceptions import IngestionError
from unidex.ingestion.file_source import FileSource

logger = logging.getLogger(__name__)

# A listing that has lost more than half of a big source is far more likely to be a
# permissions problem or a partial read than a real clean-up, so it is refused.
MIN_KEEP_RATIO = 0.5
GUARD_MIN_FILES = 20


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
        retired: Documents hidden because their file is no longer in the listing.
        restored: Documents shown again because their file is back in the listing.
    """

    seen: int
    inserted: int
    updated: int
    indexable: int
    retired: int = 0
    restored: int = 0


class SyncJob:
    """Copies one source's listing into the database, atomically."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        source: FileSource,
        department_name: str,
        source_label: str,
        clock: Callable[[], datetime] = _utc_now,
        *,
        retire_missing: bool = False,
        force: bool = False,
    ) -> None:
        """Configure a sync run.

        Args:
            conn: An open connection with the schema created.
            source: Where the file listing comes from.
            department_name: Department that owns the drive, e.g. ``"CS"``.
            source_label: Unique label for this drive, e.g. ``"CS archive"``.
            clock: Returns the current time; replaced in tests for determinism.
            retire_missing: Treat the listing as complete: hide documents whose file is no
                longer in it. Leave off for partial listings such as a CSV of one folder.
            force: Skip the safety check that refuses a listing that lost over half of a
                source.
        """
        self._conn = conn
        self._source = source
        self._department_name = department_name
        self._source_label = source_label
        self._clock = clock
        self._retire_missing = retire_missing
        self._force = force

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
            raw_files = RawFileRepository(self._conn)
            known = raw_files.count_for_source(source_id)
            if (
                self._retire_missing
                and not self._force
                and known >= GUARD_MIN_FILES
                and len(files) < known * MIN_KEEP_RATIO
            ):
                raise IngestionError(
                    f"The new listing has {len(files)} files but {known} are stored for "
                    f"{self._source_label!r}. That looks like a partial listing or a lost "
                    "permission, so nothing was changed. Use --force if it is intended."
                )
            counts = raw_files.upsert_many(source_id, files, now)
            retired = restored = 0
            if self._retire_missing:
                retired, restored = raw_files.retire_unseen(source_id, now)
            sources.mark_synced(source_id, now)

        result = SyncResult(
            seen=len(files),
            inserted=counts.inserted,
            updated=counts.updated,
            indexable=sum(1 for f in files if f.is_indexable),
            retired=retired,
            restored=restored,
        )
        logger.info(
            "Sync of %r finished: %d seen, %d new, %d updated, %d indexable, "
            "%d hidden, %d restored",
            self._source_label,
            result.seen,
            result.inserted,
            result.updated,
            result.indexable,
            result.retired,
            result.restored,
        )
        return result
