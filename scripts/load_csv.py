"""Load a drive listing CSV into the database and log a short profile of it.

Usage (from the repo root)::

    python scripts/load_csv.py --csv data/sample/sample_drive.csv
    python scripts/load_csv.py --csv data/real/Data_1_Unidex.csv
"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from unidex.config import load_settings
from unidex.db.connection import connect, init_schema
from unidex.db.repositories import RawFileRepository
from unidex.exceptions import UnidexError
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.summary import summarize
from unidex.ingestion.sync_job import SyncJob
from unidex.logging_setup import configure_logging

logger = logging.getLogger("load_csv")

DEFAULT_DEPARTMENT = "CS"
DEFAULT_LABEL = "CS archive"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True, help="drive listing CSV")
    parser.add_argument("--department", default=DEFAULT_DEPARTMENT, help="owning department")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="unique label for this drive")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            SyncJob(conn, CsvFileSource(args.csv), args.department, args.label).run()
            summary = summarize(RawFileRepository(conn).iter_all())
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Stored files: %d (%d indexable)", summary.total, summary.indexable)
    for folder, count in summary.by_course_folder.items():
        logger.info("  %-30s %5d files", folder, count)
    for extension, count in list(summary.by_extension.items())[:10]:
        logger.info("  extension %-8s %5d files", extension or "(none)", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
