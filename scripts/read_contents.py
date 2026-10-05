"""Read the text inside your PDFs so papers can be searched by topic.

Usage (from the project folder; needs a Drive sign-in and ``pip install -e ".[ocr]"``)::

    python scripts/read_contents.py --limit 10          # a small trial first
    python scripts/read_contents.py                     # past papers, solutions, tutorials
    python scripts/read_contents.py --types pyq solution notes slides --ocr rapidocr

What happens to each PDF: it is downloaded read-only, its text layer is read, pages with no text
are OCR'd (read from a picture of the page) if an OCR engine is available, and the text is
scored. Clean text is stored in your local database; scans and handwriting that cannot be read
are remembered as "unreadable" so they are not tried again. Safe to stop (Ctrl+C) and run again:
it continues where it stopped, and re-reads only files that changed in Drive.

OCR engines: ``rapidocr`` (installs with pip, nothing else needed), ``tesseract`` (needs the
Tesseract program), ``auto`` (the default: whichever works), ``none`` (text layers only).
Restart the website afterwards.
"""

import argparse
import logging
from collections.abc import Sequence

from unidex.auth.drive import DriveCredentials, load_refresh_token
from unidex.config import load_settings
from unidex.content.pipeline import DEFAULT_MAX_PAGES, ContentPipeline
from unidex.content.quality import MIN_QUALITY
from unidex.content.readers import ENGINE_AUTO, ENGINE_CHOICES, OcrReader, build_engine
from unidex.db.connection import connect, init_schema
from unidex.db.repositories import DocumentTextRepository
from unidex.exceptions import ConfigError, UnidexError
from unidex.ingestion.drive_download import DriveDownloader
from unidex.logging_setup import configure_logging

logger = logging.getLogger("read_contents")

DEFAULT_TYPES = ("pyq", "solution", "tutorial")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--types", nargs="+", default=list(DEFAULT_TYPES))
    parser.add_argument("--ocr", choices=ENGINE_CHOICES, default=ENGINE_AUTO)
    parser.add_argument("--limit", type=int, help="stop after this many files (a trial run)")
    parser.add_argument("--force", action="store_true", help="read again files that have text")
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--min-quality", type=float, default=MIN_QUALITY)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        if not (settings.drive_client_id and settings.drive_client_secret):
            raise ConfigError("Set DRIVE_CLIENT_ID and DRIVE_CLIENT_SECRET in .env first")
        engine = build_engine(args.ocr)
        logger.info("OCR engine: %s", type(engine).__name__ if engine else "none")
        credentials = DriveCredentials(
            settings.drive_client_id,
            settings.drive_client_secret,
            load_refresh_token(settings.drive_token_path),
        )
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            pipeline = ContentPipeline(
                conn,
                DriveDownloader(credentials.access_token),
                ocr=OcrReader(engine) if engine else None,
                min_quality=args.min_quality,
                max_pages=args.max_pages,
            )
            try:
                report = pipeline.run(args.types, limit=args.limit, force=args.force)
            except KeyboardInterrupt:
                logger.warning("Stopped. Run again to continue where it stopped.")
                return 1
            totals = DocumentTextRepository(conn).counts_by_method()
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    logger.info(
        "This run: %d files. Text layer %d, OCR %d, unreadable %d, download failed %d.",
        report.processed,
        report.from_text,
        report.from_ocr,
        report.unreadable,
        report.failed,
    )
    logger.info("Stored so far: %s. Restart the website to use the new text.", totals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
