"""Fill the documents table: rules first, then (optionally) Gemini for leftovers.

Usage (from the repo root)::

    python scripts/extract_metadata.py                # rules only, zero LLM calls
    python scripts/extract_metadata.py --llm gemini   # also ask Gemini about leftovers

Safe to run again: files you already confirmed are never overwritten, and
earlier Gemini answers are reused without spending requests.
"""

import argparse
import logging
from collections.abc import Sequence

from unidex.config import load_settings
from unidex.db.connection import connect, init_schema
from unidex.db.repositories import LlmUsageRepository
from unidex.exceptions import UnidexError
from unidex.extraction.budget import LlmBudget
from unidex.extraction.llm_client import GeminiClient
from unidex.extraction.llm_extractor import LlmClassifier
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.logging_setup import configure_logging

logger = logging.getLogger("extract_metadata")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--llm",
        choices=("none", "gemini"),
        default="none",
        help="use Gemini for files the rules cannot decide (default: none)",
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            classifier = None
            if args.llm == "gemini":
                client = GeminiClient(settings.gemini_api_key, settings.gemini_model)
                budget = LlmBudget(
                    LlmUsageRepository(conn),
                    settings.llm_requests_per_minute,
                    settings.llm_requests_per_day,
                )
                classifier = LlmClassifier(client, budget)
                logger.info("Gemini requests left today: %d", budget.remaining_today())
            report = ExtractionPipeline(conn, classifier).run()
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    logger.info("Documents: %d", report.total)
    for doc_type, count in report.by_doc_type.items():
        logger.info("  %-12s %5d", doc_type, count)
    logger.info(
        "Left for the language model: %d (reused %d, requests %d, filled %d)",
        report.llm_candidates,
        report.llm_reused,
        report.llm_requests,
        report.llm_filled,
    )
    logger.info("Waiting in the review queue: %d", report.review_open)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
