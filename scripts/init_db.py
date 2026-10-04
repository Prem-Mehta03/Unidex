"""Create the database schema and seed the course alias table.

Usage (from the repo root)::

    python scripts/init_db.py
    python scripts/init_db.py --aliases data/course_aliases.csv
"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from unidex.config import load_settings
from unidex.db.connection import connect, init_schema
from unidex.db.seed import seed_courses
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging

logger = logging.getLogger("init_db")

DEFAULT_ALIASES = Path("data/course_aliases.csv")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aliases", type=Path, default=DEFAULT_ALIASES, help="alias CSV to seed")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            logger.info("Schema ready at %s", settings.db_path)
            seed_courses(conn, args.aliases)
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
