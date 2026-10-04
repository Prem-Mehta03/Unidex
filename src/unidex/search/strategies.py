"""Interchangeable ways to search: a naive scan and the BM25 index.

Both implement :class:`SearchStrategy`, so the benchmark (and later the API)
can swap one for the other without changing any other code. This is the
Strategy design pattern.
"""

import heapq
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence

from unidex.models.search import ScoredDoc, SearchDocument
from unidex.search.bm25 import DEFAULT_B, DEFAULT_K1, DEFAULT_LIMIT, Bm25Scorer
from unidex.search.inverted_index import InvertedIndex
from unidex.search.tokenizer import tokenize
from unidex.search.trie import DEFAULT_SUGGESTIONS, Trie


class SearchStrategy(ABC):
    """Anything that can turn a query string into ranked results."""

    @abstractmethod
    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[ScoredDoc]:
        """Return the best-matching documents.

        Args:
            query: What the student typed.
            limit: Maximum number of results.

        Returns:
            Up to ``limit`` results, best first.
        """


class NaiveSearch(SearchStrategy):
    """Baseline: look at every document, count how many query words it contains.

    There is no index and no weighting: every query word counts the same, so a
    match on ``pdf`` is worth as much as a match on ``laplace``. Each search
    costs time proportional to the number of documents.
    """

    def __init__(self, documents: Sequence[SearchDocument]) -> None:
        """Pre-tokenise the documents (done once, not per query).

        Args:
            documents: The corpus to search.
        """
        self._tokens = [(doc.doc_id, frozenset(tokenize(doc.text))) for doc in documents]

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[ScoredDoc]:
        """Scan every document and rank by number of distinct query words found.

        Args:
            query: What the student typed.
            limit: Maximum number of results.

        Returns:
            Up to ``limit`` results, best first; ties go to the lower ``doc_id``.
        """
        terms = frozenset(tokenize(query))
        if not terms or limit <= 0:
            return []
        matches = [
            (len(terms & tokens), doc_id) for doc_id, tokens in self._tokens if terms & tokens
        ]
        best = heapq.nsmallest(limit, matches, key=lambda item: (-item[0], item[1]))
        return [ScoredDoc(doc_id=doc_id, score=float(count)) for count, doc_id in best]


class Bm25Search(SearchStrategy):
    """Search with an inverted index, BM25 ranking and trie autocomplete."""

    def __init__(
        self,
        documents: Sequence[SearchDocument],
        k1: float = DEFAULT_K1,
        b: float = DEFAULT_B,
    ) -> None:
        """Build the index and the autocomplete trie (done once, not per query).

        Args:
            documents: The corpus to search.
            k1: BM25 term-frequency saturation.
            b: BM25 length normalisation.
        """
        index = InvertedIndex()
        word_counts: Counter[str] = Counter()
        for doc in documents:
            index.add_document(doc.doc_id, tokenize(doc.text))
            word_counts.update(set(tokenize(doc.text, stem=False)))
        self._scorer = Bm25Scorer(index, k1=k1, b=b)
        self._autocomplete = Trie()
        for word, document_count in word_counts.items():
            self._autocomplete.insert(word, document_count)

    def search(self, query: str, limit: int = DEFAULT_LIMIT) -> list[ScoredDoc]:
        """Rank documents with BM25.

        Args:
            query: What the student typed.
            limit: Maximum number of results.

        Returns:
            Up to ``limit`` results, best first.
        """
        return self._scorer.search(tokenize(query), limit=limit)

    def suggest(self, prefix: str, limit: int = DEFAULT_SUGGESTIONS) -> list[str]:
        """Suggest words that complete what the student has typed so far.

        Args:
            prefix: The partial word, e.g. ``"lap"``.
            limit: Maximum number of suggestions.

        Returns:
            Words that start with the prefix, most common first.
        """
        cleaned = prefix.strip().casefold()
        if not cleaned:
            return []
        return self._autocomplete.complete(cleaned, limit=limit)
