"""Compare naive scanning with the BM25 index on quality and speed.

Quality: for each test query, is a correct file in the top K results (hit@K)?
Speed: mean time per query as the corpus grows. The corpus is made larger by
repeating every real document ``scale`` times, so the vocabulary stays realistic.

Usage (from the repo root, after loading a CSV with ``scripts/load_csv.py``)::

    python scripts/benchmark.py
    python scripts/benchmark.py --scales 1 10 100 1000 --repeats 20
"""

import argparse
import csv
import logging
import time
from collections.abc import Sequence
from pathlib import Path

from unidex.config import load_settings
from unidex.db.connection import connect
from unidex.db.repositories import RawFileRepository
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging
from unidex.models.search import SearchDocument
from unidex.search.corpus import build_corpus
from unidex.search.evaluation import EvalQuery, first_relevant_rank, load_queries
from unidex.search.strategies import Bm25Search, NaiveSearch, SearchStrategy

logger = logging.getLogger("benchmark")

DEFAULT_QUERIES = Path("eval/queries.csv")
DEFAULT_OUTPUT = Path("eval/benchmark_results.csv")
DEFAULT_SCALES = (1, 10, 100)
DEFAULT_REPEATS = 10
DEFAULT_TOP_K = 5
MRR_CUTOFF = 10
MILLISECONDS = 1000.0


def scale_corpus(documents: Sequence[SearchDocument], factor: int) -> list[SearchDocument]:
    """Make a corpus ``factor`` times larger by repeating each document.

    Each copy gets a unique extra word in its title so copies are distinct.

    Args:
        documents: The real documents.
        factor: How many copies of each document to create (1 keeps it as is).

    Returns:
        The enlarged corpus with consecutive document ids.
    """
    if factor == 1:
        return list(documents)
    scaled: list[SearchDocument] = []
    for copy in range(factor):
        for doc in documents:
            scaled.append(
                SearchDocument(
                    doc_id=len(scaled),
                    title=f"{doc.title} copy{copy}",
                    path=doc.path,
                    url=doc.url,
                )
            )
    return scaled


def mean_query_ms(strategy: SearchStrategy, queries: Sequence[EvalQuery], repeats: int) -> float:
    """Measure the average time of one search, in milliseconds.

    Args:
        strategy: The strategy to time.
        queries: Queries to run.
        repeats: How many times to run the whole query list.

    Returns:
        Mean milliseconds per search.
    """
    for query in queries:  # warm-up run, not timed
        strategy.search(query.text, limit=DEFAULT_TOP_K)
    started = time.perf_counter()
    for _ in range(repeats):
        for query in queries:
            strategy.search(query.text, limit=DEFAULT_TOP_K)
    elapsed = time.perf_counter() - started
    return elapsed / (repeats * len(queries)) * MILLISECONDS


def report_quality(
    documents: Sequence[SearchDocument], queries: Sequence[EvalQuery], top_k: int
) -> None:
    """Log the rank of the first correct file per query, plus summary metrics.

    Summary metrics per strategy: hit@1 (right file first), hit@K (right file in
    the top K) and MRR@10 (mean reciprocal rank over the top 10).

    Args:
        documents: The real corpus.
        queries: Test queries.
        top_k: How many results count as "found" for hit@K.
    """
    by_id = {doc.doc_id: doc for doc in documents}
    strategies = {"naive": NaiveSearch(documents), "bm25": Bm25Search(documents)}
    first = dict.fromkeys(strategies, 0)
    found = dict.fromkeys(strategies, 0)
    reciprocal = dict.fromkeys(strategies, 0.0)
    logger.info("Quality: rank of the first correct file (- = not in the top %d)", MRR_CUTOFF)
    for query in queries:
        marks = []
        for name, strategy in strategies.items():
            rank = first_relevant_rank(strategy, by_id, query, MRR_CUTOFF)
            first[name] += int(rank == 1)
            found[name] += int(rank is not None and rank <= top_k)
            reciprocal[name] += 0.0 if rank is None else 1.0 / rank
            marks.append(f"{name}={'-' if rank is None else rank}")
        logger.info("  %-40s %s", query.text, "  ".join(marks))
    for name in strategies:
        logger.info(
            "  %-6s hit@1 %d/%d   hit@%d %d/%d   MRR@%d %.3f",
            name,
            first[name],
            len(queries),
            top_k,
            found[name],
            len(queries),
            MRR_CUTOFF,
            reciprocal[name] / len(queries),
        )


def report_speed(
    documents: Sequence[SearchDocument],
    queries: Sequence[EvalQuery],
    scales: Sequence[int],
    repeats: int,
    output: Path,
) -> None:
    """Log and save build time and query time for growing corpus sizes.

    Args:
        documents: The real corpus.
        queries: Queries to time.
        scales: Corpus multipliers to try.
        repeats: Repetitions of the query list per measurement.
        output: CSV file the results are written to.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Speed: mean time per query")
    largest: dict[str, SearchStrategy] = {}
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["documents", "strategy", "build_ms", "query_ms"])
        for scale in scales:
            corpus = scale_corpus(documents, scale)
            for name, factory in (("naive", NaiveSearch), ("bm25", Bm25Search)):
                started = time.perf_counter()
                strategy = factory(corpus)
                build_ms = (time.perf_counter() - started) * MILLISECONDS
                largest[name] = strategy
                query_ms = mean_query_ms(strategy, queries, repeats)
                writer.writerow([len(corpus), name, f"{build_ms:.2f}", f"{query_ms:.4f}"])
                logger.info(
                    "  %8d docs  %-6s build %9.1f ms   query %8.4f ms",
                    len(corpus),
                    name,
                    build_ms,
                    query_ms,
                )
    logger.info("Saved %s", output)
    logger.info("Per query at the largest size (selective words favour the index):")
    for query in queries:
        naive_ms = mean_query_ms(largest["naive"], [query], repeats)
        bm25_ms = mean_query_ms(largest["bm25"], [query], repeats)
        logger.info("  %-40s naive %8.3f ms   bm25 %8.3f ms", query.text, naive_ms, bm25_ms)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--scales", type=int, nargs="+", default=list(DEFAULT_SCALES))
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        queries = load_queries(args.queries)
        conn = connect(settings.db_path)
        try:
            corpus = build_corpus(RawFileRepository(conn).iter_all())
        finally:
            conn.close()
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    if not corpus or not queries:
        logger.error("Need at least one indexable file and one query.")
        return 1
    logger.info("%d documents, %d test queries", len(corpus), len(queries))

    report_quality(corpus, queries, args.top_k)
    report_speed(corpus, queries, args.scales, args.repeats, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
