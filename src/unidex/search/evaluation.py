"""Measure search quality against a hand-written list of test queries.

A query file is a CSV with the columns ``query, must_contain_name,
must_contain_path, note``. A result counts as *relevant* if its file name
contains ``must_contain_name`` and its folder path contains
``must_contain_path`` (both case-insensitive; an empty value matches anything).
A query is a *hit* if any of the top ``k`` results is relevant ("hit@k").
"""

import csv
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from unidex.exceptions import IngestionError
from unidex.models.search import SearchDocument
from unidex.search.strategies import SearchStrategy

REQUIRED_COLUMNS = ("query", "must_contain_name", "must_contain_path")


@dataclass(frozen=True, slots=True)
class EvalQuery:
    """One test query and what a correct result looks like.

    Attributes:
        text: The query a student would type.
        name_part: Text the correct file's name must contain (may be empty).
        path_part: Text the correct file's path must contain (may be empty).
    """

    text: str
    name_part: str
    path_part: str


def load_queries(csv_path: Path) -> list[EvalQuery]:
    """Read test queries from a CSV file.

    Args:
        csv_path: Path to the query CSV.

    Returns:
        The queries, in file order.

    Raises:
        IngestionError: If the file is missing or lacks a required column.
    """
    if not csv_path.is_file():
        raise IngestionError(f"Query file not found: {csv_path}")
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise IngestionError(f"{csv_path} is missing columns: {', '.join(missing)}")
        return [
            EvalQuery(
                text=row["query"].strip(),
                name_part=row["must_contain_name"].strip(),
                path_part=row["must_contain_path"].strip(),
            )
            for row in reader
            if row["query"].strip()
        ]


def is_relevant(doc: SearchDocument, query: EvalQuery) -> bool:
    """Return whether a document is a correct answer for a test query.

    Args:
        doc: A search result's document.
        query: The test query.

    Returns:
        True if the name and path both contain the expected text.
    """
    return query.name_part.casefold() in doc.title.casefold() and (
        query.path_part.casefold() in doc.path.casefold()
    )


def first_relevant_rank(
    strategy: SearchStrategy,
    documents: Mapping[int, SearchDocument],
    query: EvalQuery,
    k: int,
) -> int | None:
    """Return the position (1 = best) of the first correct result in the top ``k``.

    Args:
        strategy: The search strategy under test.
        documents: Every document keyed by ``doc_id``.
        query: The test query.
        k: How many top results to inspect.

    Returns:
        The 1-based rank of the first relevant result, or ``None`` if none of
        the top ``k`` is relevant.
    """
    for rank, hit in enumerate(strategy.search(query.text, limit=k), start=1):
        if is_relevant(documents[hit.doc_id], query):
            return rank
    return None


def hit_at_k(
    strategy: SearchStrategy,
    documents: Mapping[int, SearchDocument],
    query: EvalQuery,
    k: int,
) -> bool:
    """Return whether a correct result appears in the top ``k``.

    Args:
        strategy: The search strategy under test.
        documents: Every document keyed by ``doc_id``.
        query: The test query.
        k: How many top results to inspect.

    Returns:
        True if any of the top ``k`` results is relevant.
    """
    return first_relevant_rank(strategy, documents, query, k) is not None


def reciprocal_rank(
    strategy: SearchStrategy,
    documents: Mapping[int, SearchDocument],
    query: EvalQuery,
    k: int,
) -> float:
    """Return ``1 / rank`` of the first correct result (0.0 if not in the top ``k``).

    Averaged over many queries this is the *mean reciprocal rank* (MRR): 1.0
    means the right file is always first, 0.5 means it is second on average.

    Args:
        strategy: The search strategy under test.
        documents: Every document keyed by ``doc_id``.
        query: The test query.
        k: How many top results to inspect.

    Returns:
        The reciprocal rank, between 0.0 and 1.0.
    """
    rank = first_relevant_rank(strategy, documents, query, k)
    return 0.0 if rank is None else 1.0 / rank
