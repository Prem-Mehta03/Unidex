"""BM25 ranking over an inverted index."""

import heapq
import math
from collections import defaultdict
from collections.abc import Collection, Sequence

from unidex.models.search import ScoredDoc
from unidex.search.inverted_index import InvertedIndex

DEFAULT_K1 = 1.5
DEFAULT_B = 0.75
DEFAULT_LIMIT = 10


class Bm25Scorer:
    """Ranks documents for a query using the BM25 formula.

    For each query term that a document contains, BM25 adds::

        idf(term) * tf * (k1 + 1) / (tf + k1 * (1 - b + b * doc_len / avg_len))

    * ``idf`` rewards rare terms: ``laplace`` says more than ``pdf``.
    * ``tf`` is how often the term occurs in the document, with diminishing
      returns controlled by ``k1``.
    * The ``doc_len / avg_len`` part stops long documents winning just by
      being long; ``b`` controls how strongly.

    A document's score is the sum of that expression over the query's terms.
    """

    def __init__(self, index: InvertedIndex, k1: float = DEFAULT_K1, b: float = DEFAULT_B) -> None:
        """Create a scorer.

        Args:
            index: The inverted index to search.
            k1: Term-frequency saturation (typical range 1.2 to 2.0).
            b: Length normalisation from 0 (off) to 1 (full).

        Raises:
            ValueError: If ``k1`` is negative or ``b`` is outside ``[0, 1]``.
        """
        if k1 < 0:
            raise ValueError(f"k1 must not be negative (got {k1})")
        if not 0.0 <= b <= 1.0:
            raise ValueError(f"b must be between 0 and 1 (got {b})")
        self._index = index
        self._k1 = k1
        self._b = b

    def idf(self, term: str) -> float:
        """Return the inverse document frequency of a term.

        Uses ``ln(1 + (N - n + 0.5) / (n + 0.5))`` where ``N`` is the number of
        documents in the index and ``n`` the number containing the term. This
        form is never negative, even for a term that appears in every document.

        Args:
            term: A token.

        Returns:
            The idf weight.
        """
        total = self._index.num_docs
        containing = self._index.doc_freq(term)
        return math.log(1 + (total - containing + 0.5) / (containing + 0.5))

    def search(
        self,
        query_tokens: Sequence[str],
        limit: int = DEFAULT_LIMIT,
        allowed_ids: Collection[int] | None = None,
    ) -> list[ScoredDoc]:
        """Return the best-matching documents for a query.

        Method ("term at a time"): keep a running score per document; for each
        distinct query term, walk that term's postings and add the term's
        contribution to every document in them. Only documents containing at
        least one query term are ever touched, so the work depends on the
        length of the query terms' postings, not on the size of the corpus.
        The best ``limit`` documents are then picked with a heap in
        O(m log limit) rather than sorting all m scored documents.

        Behaviour:

        * Repeated query terms count once.
        * Terms not in the index contribute nothing.
        * Documents that match no query term are never returned.
        * Results are sorted best score first; ties go to the lower ``doc_id``.
        * At most ``limit`` results; ``limit <= 0`` returns an empty list.
        * If ``allowed_ids`` is given, only those document ids may appear
          (pass a ``set`` so each membership check is O(1)).

        Args:
            query_tokens: Tokenised query.
            limit: Maximum number of results.
            allowed_ids: If given, only these document ids may be returned.

        Returns:
            Up to ``limit`` results, best first.
        """
        if limit <= 0:
            return []

        # Everything that does not change from posting to posting is computed once,
        # outside the loops: this is the hottest code in the whole search.
        lengths = self._index.doc_lengths
        avg_length = self._index.avg_doc_length
        k1 = self._k1
        saturation = k1 + 1
        norm_base = 1 - self._b
        norm_slope = self._b / avg_length if avg_length else 0.0

        scores: defaultdict[int, float] = defaultdict(float)
        # dict.fromkeys removes duplicates but keeps first-seen order, so scores
        # are added in the same order every run (a set would not guarantee that).
        for term in dict.fromkeys(query_tokens):
            postings = self._index.postings(term)
            if not postings:
                continue
            idf = self.idf(term)
            for doc_id, tf in postings.items():
                if allowed_ids is not None and doc_id not in allowed_ids:
                    continue
                length_norm = norm_base + norm_slope * lengths[doc_id]
                scores[doc_id] += idf * tf * saturation / (tf + k1 * length_norm)

        best = heapq.nsmallest(limit, scores.items(), key=lambda item: (-item[1], item[0]))
        return [ScoredDoc(doc_id=doc_id, score=score) for doc_id, score in best]
