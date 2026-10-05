"""Copy a Drive folder's file listing into the database (nothing is copied from the files).

Usage (from the project folder, after ``python scripts/drive_login.py``)::

    python scripts/sync_drive.py --folder <folder id or link> --dry-run
    python scripts/sync_drive.py --folder <id> --compare data/real/Data_1_Unidex.csv --dry-run
    python scripts/sync_drive.py --folder <M3 id> --path-prefix /M3 \
        --compare data/real/Data_2_M3_Unidex.csv --dry-run
    python scripts/sync_drive.py --folder <id> --department CS --label "CS archive"

Start with ``--dry-run`` (lists and prints, writes nothing). ``--compare`` checks the Drive
listing against an older CSV export: same files, same folder paths. A real run stores the
listing, hides documents whose file is gone from Drive, then runs the rule-based metadata
extraction. Restart the website afterwards so it picks up the new data.
"""

import argparse
import logging
from collections.abc import Iterator, Sequence
from pathlib import Path

from unidex.auth.drive import DriveCredentials, load_refresh_token
from unidex.config import Settings, load_settings
from unidex.db.connection import connect, init_schema
from unidex.exceptions import ConfigError, UnidexError
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.ingestion.compare import compare_listings
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.drive_source import DriveFileSource
from unidex.ingestion.file_source import FileSource
from unidex.ingestion.sync_job import SyncJob
from unidex.logging_setup import configure_logging
from unidex.models.raw_file import RawFile

logger = logging.getLogger("sync_drive")

DEFAULT_DEPARTMENT = "CS"
DEFAULT_LABEL = "CS archive"
SAMPLE_PATHS = 5


def build_source(settings: Settings, folder: str, path_prefix: str = "") -> DriveFileSource:
    """Create the Drive source from the saved sign-in.

    Args:
        settings: Application settings.
        folder: Folder id or link.
        path_prefix: Folder names to put in front of every path (for example ``/M3``).

    Returns:
        A source ready to list.

    Raises:
        ConfigError: If the Drive client id and secret are not configured.
        AuthError: If no saved sign-in exists.
    """
    if not (settings.drive_client_id and settings.drive_client_secret):
        raise ConfigError("Set DRIVE_CLIENT_ID and DRIVE_CLIENT_SECRET in .env first")
    credentials = DriveCredentials(
        settings.drive_client_id,
        settings.drive_client_secret,
        load_refresh_token(settings.drive_token_path),
    )
    return DriveFileSource(credentials.access_token, folder, path_prefix=path_prefix)


def report_listing(files: Sequence[RawFile]) -> None:
    """Log a short profile of a listing."""
    indexable = sum(1 for f in files if f.is_indexable)
    logger.info("Listed %d files (%d look like study material)", len(files), indexable)
    for file in files[:SAMPLE_PATHS]:
        logger.info("  e.g. %s/%s", file.path.rstrip("/"), file.name)


def report_comparison(files: Sequence[RawFile], csv_path: Path) -> None:
    """Log how the Drive listing differs from an older CSV export."""
    result = compare_listings(files, CsvFileSource(csv_path).fetch())
    logger.info(
        "Compared with %s: %d files in both (%d with identical folder and name), "
        "%d only in Drive, %d only in the CSV",
        csv_path.name,
        result.in_both,
        result.same_path,
        result.only_new,
        result.only_old,
    )
    for old, new in result.path_examples:
        logger.info("  path differs: CSV %s  |  Drive %s", old, new)
    for path in result.only_new_examples:
        logger.info("  only in Drive: %s", path)
    for path in result.only_old_examples:
        logger.info("  only in CSV: %s", path)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--folder", required=True, help="Drive folder id or link")
    parser.add_argument("--department", default=DEFAULT_DEPARTMENT, help="owning department")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="unique label for this drive")
    parser.add_argument(
        "--path-prefix",
        default="",
        help="folders above the chosen folder, e.g. /M3 when --folder is the M3 folder",
    )
    parser.add_argument("--compare", type=Path, help="older CSV export to compare against")
    parser.add_argument("--dry-run", action="store_true", help="list only; write nothing")
    parser.add_argument("--force", action="store_true", help="accept a listing that lost files")
    parser.add_argument("--skip-extract", action="store_true", help="do not run extraction")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        source = build_source(settings, args.folder, args.path_prefix)
        files = list(source.fetch())
        report_listing(files)
        if args.compare:
            report_comparison(files, args.compare)
        if args.dry_run:
            logger.info("Dry run: nothing was written.")
            return 0
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            job = SyncJob(
                conn,
                _Listed(files),
                args.department,
                args.label,
                retire_missing=True,
                force=args.force,
            )
            result = job.run()
            if not args.skip_extract:
                report = ExtractionPipeline(conn).run()
                logger.info(
                    "Documents: %d (%d waiting in the review queue)",
                    report.total,
                    report.review_open,
                )
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "Done: %d new, %d updated, %d hidden (gone from Drive), %d restored. "
        "Restart the website to see the changes.",
        result.inserted,
        result.updated,
        result.retired,
        result.restored,
    )
    return 0


class _Listed(FileSource):
    """A source that replays an already fetched listing (so we list Drive only once)."""

    def __init__(self, files: Sequence[RawFile]) -> None:
        self._files = files

    def fetch(self) -> Iterator[RawFile]:
        """Yield the stored listing."""
        yield from self._files


if __name__ == "__main__":
    raise SystemExit(main())
