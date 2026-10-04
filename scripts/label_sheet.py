"""Build a sheet of ~100 files to check by hand, and score it afterwards.

Usage (from the repo root)::

    python scripts/label_sheet.py make
    python scripts/label_sheet.py score --file data/real/label_sheet.csv

``make`` writes data/real/label_sheet.csv. In the sheet, put y or n in the
"correct" column of each row. For an n, fill the fix_* column(s) that were
wrong. Rows left blank are ignored.
"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from unidex.config import load_settings
from unidex.db.connection import connect, init_schema
from unidex.db.repositories import iter_document_views
from unidex.exceptions import UnidexError
from unidex.extraction.labelling import (
    DEFAULT_SIZE,
    FIELDS,
    build_sheet_rows,
    score_sheet,
    write_sheet,
)
from unidex.logging_setup import configure_logging

logger = logging.getLogger("label_sheet")

DEFAULT_FILE = Path("data/real/label_sheet.csv")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make")
    make.add_argument("--file", type=Path, default=DEFAULT_FILE)
    make.add_argument("--size", type=int, default=DEFAULT_SIZE)
    score = sub.add_parser("score")
    score.add_argument("--file", type=Path, default=DEFAULT_FILE)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        if args.command == "make":
            conn = connect(settings.db_path)
            try:
                init_schema(conn)
                views = list(iter_document_views(conn))
            finally:
                conn.close()
            write_sheet(build_sheet_rows(views, size=args.size), args.file)
        else:
            report = score_sheet(args.file)
            logger.info("Rows left unchecked: %d", report.unchecked)
            for stratum, result in report.strata.items():
                logger.info(
                    "[%s] %d rows checked, %d fully correct (%.0f%%)",
                    stratum,
                    result.rows,
                    result.rows_correct,
                    100 * result.rows_correct / result.rows if result.rows else 0,
                )
                for name in FIELDS:
                    field_score = result.fields[name]
                    logger.info(
                        "    %-14s %3d / %3d  (%.0f%%)",
                        name,
                        field_score.right,
                        field_score.total,
                        100 * field_score.accuracy,
                    )
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
