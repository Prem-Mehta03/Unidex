"""Print student reports (wrong info / broken link) and, optionally, trim old search logs.

Usage (from the project folder)::

    python scripts/list_reports.py                 # newest 50 reports
    python scripts/list_reports.py --limit 200
    python scripts/list_reports.py --delete-logs-older-than 90   # days

Search logs hold what people typed plus a one-way label for the person (never an email).
Trim them regularly: they are only needed to measure how well search works.
"""

import argparse
import logging
from datetime import UTC, datetime, timedelta

from unidex.config import load_settings
from unidex.db.connection import connect
from unidex.db.repositories import ReportRepository, SearchLogRepository
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging

logger = logging.getLogger("list_reports")

DEFAULT_LIMIT = 50


def main() -> int:
    """Run the command-line tool.

    Returns:
        The process exit code: 0 on success, 1 on a known error.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="reports to show")
    parser.add_argument(
        "--delete-logs-older-than",
        type=int,
        metavar="DAYS",
        help="delete search-log rows older than this many days",
    )
    args = parser.parse_args()
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            if args.delete_logs_older_than is not None:
                cutoff = datetime.now(UTC) - timedelta(days=args.delete_logs_older_than)
                removed = SearchLogRepository(conn).delete_before(cutoff.isoformat())
                logger.info("Deleted %d search-log rows", removed)
            rows = ReportRepository(conn).recent(args.limit)
        finally:
            conn.close()
    except UnidexError:
        logger.exception("Could not read reports")
        return 1
    if not rows:
        logger.info("No reports yet.")
    for row in rows:
        note = f" - {row.note}" if row.note else ""
        logger.info(
            "#%d %s %s [%s] %s%s", row.id, row.created_at, row.type, row.name, row.url, note
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
