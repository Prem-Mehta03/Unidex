"""Try the search engine from the command line.

Usage (from the repo root, after loading a CSV with ``scripts/load_csv.py``)::

    python scripts/search_cli.py "m3 midsem solutions"
    python scripts/search_cli.py "laplace guide" --strategy naive --limit 5
    python scripts/search_cli.py --suggest lap
"""

import argparse
import logging
from collections.abc import Sequence

from unidex.config import load_settings
from unidex.db.connection import connect
from unidex.db.repositories import RawFileRepository
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging
from unidex.search.corpus import build_corpus
from unidex.search.strategies import Bm25Search, NaiveSearch

logger = logging.getLogger("search_cli")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="", help="what to search for")
    parser.add_argument("--strategy", choices=("bm25", "naive"), default="bm25")
    parser.add_argument("--limit", type=int, default=10, help="maximum number of results")
    parser.add_argument("--suggest", default="", help="show autocomplete suggestions for a prefix")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            corpus = build_corpus(RawFileRepository(conn).iter_all())
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    if not corpus:
        logger.error("No indexable files found. Run scripts/load_csv.py first.")
        return 1
    logger.info("Searching %d documents", len(corpus))

    bm25 = Bm25Search(corpus)
    if args.suggest:
        logger.info("Suggestions for %r: %s", args.suggest, ", ".join(bm25.suggest(args.suggest)))
    if args.query:
        strategy = bm25 if args.strategy == "bm25" else NaiveSearch(corpus)
        hits = strategy.search(args.query, limit=args.limit)
        logger.info("%d result(s) for %r using %s", len(hits), args.query, args.strategy)
        for rank, hit in enumerate(hits, start=1):
            doc = corpus[hit.doc_id]
            logger.info("%2d. %6.3f  %s  [%s]", rank, hit.score, doc.title, doc.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
