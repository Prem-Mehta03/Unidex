"""Helpers for measuring search.

Covers percentiles, rank statistics, scaled corpora and a self-retrieval test for text read
from inside files.

The self-retrieval test needs no hand-written answers: for a sample of readable documents it
picks a few words that are rare across the collection and frequent in that one document, asks
the search for them, and checks whether the document comes back. It shows that the content
index finds what it holds (an upper bound), not that students will phrase queries this way.
"""

import math
import random
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from unidex.models.search import SearchDocument
from unidex.search.tokenizer import tokenize

MIN_TERM_LETTERS = 5
MAX_DOC_SHARE = 0.05  # a "distinctive" word appears in at most 5% of the documents
MIN_TERM_COUNT = 2  # ... and at least this many times in the chosen document


@dataclass(frozen=True, slots=True)
class RankStats:
    """Summary of the rank of the right answer over many queries.

    Attributes:
        total: Number of queries.
        hit_at_1: Queries whose right answer came first.
        hit_at_k: Queries whose right answer was in the top ``k``.
        k: The ``k`` used for ``hit_at_k``.
        mrr: Mean reciprocal rank (0 for queries with no rank).
    """

    total: int
    hit_at_1: int
    hit_at_k: int
    k: int
    mrr: float


@dataclass(frozen=True, slots=True)
class SelfQuery:
    """A generated query and the file it should find.

    Attributes:
        query: The words to search for.
        drive_file_id: The file those words came from.
    """

    query: str
    drive_file_id: str


def percentile(values: Sequence[float], p: float) -> float:
    """Return the ``p``-th percentile (0 to 100) using linear interpolation.

    Args:
        values: At least one number.
        p: Percentile between 0 and 100.

    Returns:
        The interpolated value.

    Raises:
        ValueError: If ``values`` is empty or ``p`` is outside 0 to 100.
    """
    if not values:
        raise ValueError("percentile needs at least one value")
    if not 0 <= p <= 100:
        raise ValueError("p must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * p / 100
    low = math.floor(position)
    high = math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def rank_stats(ranks: Iterable[int | None], k: int) -> RankStats:
    """Summarise 1-based ranks (``None`` means not found).

    Args:
        ranks: One rank per query.
        k: The cut-off for ``hit_at_k``.

    Returns:
        Counts and mean reciprocal rank.
    """
    listed = list(ranks)
    found = [r for r in listed if r is not None]
    return RankStats(
        total=len(listed),
        hit_at_1=sum(1 for r in found if r == 1),
        hit_at_k=sum(1 for r in found if r <= k),
        k=k,
        mrr=(sum(1.0 / r for r in found) / len(listed)) if listed else 0.0,
    )


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


def self_queries(
    texts: Mapping[str, str],
    names: Mapping[str, str],
    *,
    sample: int,
    seed: int = 1,
    words_per_query: int = 3,
) -> list[SelfQuery]:
    """Generate queries from the files' own text.

    Args:
        texts: ``{drive file id: text}`` for the readable files.
        names: ``{drive file id: file name}``; words in the name are not used, so a hit proves
            the *content* index worked, not the name index.
        sample: How many files to draw (all of them if fewer qualify).
        seed: Random seed, so the same sample is drawn each time.
        words_per_query: Rare words per query.

    Returns:
        One query per sampled file that has enough rare words.
    """
    stems = {fid: set(tokenize(text)) for fid, text in texts.items()}
    doc_freq: Counter[str] = Counter()
    for found in stems.values():
        doc_freq.update(found)
    limit = max(2, int(len(texts) * MAX_DOC_SHARE))
    chosen = random.Random(seed).sample(sorted(texts), min(sample, len(texts)))  # noqa: S311
    queries: list[SelfQuery] = []
    for fid in sorted(chosen):
        in_name = set(tokenize(names.get(fid, "")))
        counts = Counter(t for t in tokenize(texts[fid]) if _is_word(t))
        candidates = [
            (term, n)
            for term, n in counts.items()
            if n >= MIN_TERM_COUNT and 1 <= doc_freq[term] <= limit and term not in in_name
        ]
        candidates.sort(key=lambda item: (doc_freq[item[0]], -item[1], item[0]))
        if len(candidates) >= words_per_query:
            words = [term for term, _ in candidates[:words_per_query]]
            queries.append(SelfQuery(query=" ".join(words), drive_file_id=fid))
    return queries


def _is_word(token: str) -> bool:
    """Whether a token is a real-looking word (letters only, long enough)."""
    return len(token) >= MIN_TERM_LETTERS and token.isalpha()
