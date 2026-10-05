"""Score the chat's message reading against ``eval/chat_messages.csv``.

Usage (from the repo root, after loading CSVs and running the extractor)::

    python scripts/eval_chat.py
    python scripts/eval_chat.py --file eval/my_messages.csv --show-all

Uses rules only: no language-model requests are made.
"""

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from unidex.api.app import load_catalog
from unidex.chat.evaluation import FIELDS, evaluate, load_cases
from unidex.chat.service import ChatService
from unidex.config import load_settings
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging

logger = logging.getLogger("eval_chat")
DEFAULT_FILE = Path("eval/chat_messages.csv")


def _show(value: object) -> object:
    """Make sets readable and in a stable order."""
    return sorted(map(str, value)) if isinstance(value, frozenset) else value


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE, help="messages CSV")
    parser.add_argument("--show-all", action="store_true", help="list passing cases too")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        service = ChatService(load_catalog(settings))
        cases = load_cases(args.file)
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    report = evaluate(cases, service.reply)
    asked, should = report.clarifications
    logger.info("Messages: %d (%s)", report.total, args.file)
    logger.info("Reply kind correct:   %d / %d", report.kinds_correct, report.total)
    logger.info("Asked when it should: %d / %d", asked, should)
    logger.info("Whole form correct:   %d / %d", report.forms_correct, report.total)
    for name in FIELDS:
        logger.info("  %-13s %d / %d", name, report.field_correct(name), report.total)
    for outcome in report.outcomes:
        if outcome.form_ok and outcome.kind_ok and not args.show_all:
            continue
        logger.info("")
        logger.info(
            "%s  %r",
            "OK  " if outcome.form_ok and outcome.kind_ok else "MISS",
            outcome.case.message,
        )
        if not outcome.kind_ok:
            logger.info("   kind: expected %s, got %s", outcome.case.kind, outcome.kind)
        for name in outcome.wrong_fields:
            logger.info(
                "   %s: expected %s, got %s",
                name,
                _show(outcome.case.expected[name]),
                _show(outcome.actual[name]),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
