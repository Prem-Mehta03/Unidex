"""Write ``deploy/catalog.db``: the database to upload with the code to the web host.

Usage (from the repo root, after syncing, extracting and reading contents)::

    python scripts/export_deploy_db.py

The copy has no reports, search logs or model-usage counts. Commit it to a PRIVATE
repository only: it contains your drives' file names, links and paper text.
"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from unidex.config import load_settings
from unidex.db.export import GITHUB_WARN_BYTES, ExportError, export_snapshot
from unidex.logging_setup import configure_logging

logger = logging.getLogger("export_deploy_db")
DEFAULT_TARGET = Path("deploy/catalog.db")
MEGABYTE = 1024 * 1024


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_TARGET)
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        info = export_snapshot(settings.db_path, args.output)
    except ExportError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "Wrote %s: %.1f MB, %d documents, %d with text",
        info.path,
        info.size_bytes / MEGABYTE,
        info.documents,
        info.texts,
    )
    if info.size_bytes > GITHUB_WARN_BYTES:
        logger.warning("Files over 50 MB are slow on GitHub (the hard limit is 100 MB)")
    logger.info("Commit it to a PRIVATE repository, then let the host redeploy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
