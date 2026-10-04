"""Export the review queue to a spreadsheet, and import your corrections.

Usage (from the repo root)::

    python scripts/review_queue.py export                       # writes data/real/review.csv
    python scripts/review_queue.py import --file data/real/review.csv

Edit the doc_type, exam_type, exam_number, academic_year, is_makeup,
has_solution and syllabus_scope columns. Delete rows you are unsure about.
Every row left in the file is stored as confirmed by you.
"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from unidex.config import load_settings
from unidex.db.connection import connect, init_schema
from unidex.exceptions import UnidexError
from unidex.extraction.review_io import export_review, import_review
from unidex.logging_setup import configure_logging

logger = logging.getLogger("review_queue")

DEFAULT_FILE = Path("data/real/review.csv")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "import"):
        command = sub.add_parser(name)
        command.add_argument("--file", type=Path, default=DEFAULT_FILE, help="review CSV path")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            if args.command == "export":
                export_review(conn, args.file)
            else:
                import_review(conn, args.file)
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
