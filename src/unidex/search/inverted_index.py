"""Inverted index: the data structure behind every search engine."""

from collections import Counter
from collections.abc import Mapping, Sequence
from types import MappingProxyType


class InvertedIndex:
    """Maps each term to the documents that contain it.

    A normal index maps document to words. An *inverted* index flips this: word
    to documents. To answer "which documents contain ``laplace``?", we look up
    one dictionary key instead of scanning every document.

    Example of what the index holds after two ``add_document`` calls::

        add_document(0, ["laplace", "transform", "guide"])
        add_document(1, ["laplace", "laplace", "note"])

        "laplace"   -> doc 0 appears 1 time, doc 1 appears 2 times
        "transform" -> doc 0 appears 1 time
        "guide"     -> doc 0 appears 1 time
        "note"      -> doc 1 appears 1 time

    That "term -> {document: how many times}" table is called the *postings*.
    Besides it, the index remembers each document's length (its number of
    tokens) and the running total of all lengths, because BM25 needs both.

    Costs: adding a document is O(its tokens); looking up a term's postings,
    its document frequency, a document's length or the average length is O(1).
    """

    def __init__(self) -> None:
        """Create an empty index."""
        self._postings: dict[str, dict[int, int]] = {}
        self._doc_lengths: dict[int, int] = {}
        self._total_length = 0

    def add_document(self, doc_id: int, tokens: Sequence[str]) -> None:
        """Add one document's tokens to the index.

        Args:
            doc_id: Unique identifier of the document.
            tokens: The document's tokens, duplicates included. May be empty.

        Raises:
            ValueError: If ``doc_id`` is already in the index.
        """
        if doc_id in self._doc_lengths:
            raise ValueError(f"Document {doc_id} is already in the index")
        self._doc_lengths[doc_id] = len(tokens)
        self._total_length += len(tokens)
        for term, frequency in Counter(tokens).items():
            self._postings.setdefault(term, {})[doc_id] = frequency

    @property
    def num_docs(self) -> int:
        """Number of documents in the index (empty documents count)."""
        return len(self._doc_lengths)

    @property
    def avg_doc_length(self) -> float:
        """Average number of tokens per document, or 0.0 for an empty index."""
        if not self._doc_lengths:
            return 0.0
        return self._total_length / len(self._doc_lengths)

    @property
    def doc_lengths(self) -> Mapping[int, int]:
        """A read-only view of ``{doc_id: token count}`` for every document.

        Scoring loops use this instead of calling :meth:`doc_length` once per
        posting, which avoids a method call in the hottest loop of the search.
        """
        return MappingProxyType(self._doc_lengths)

    def doc_length(self, doc_id: int) -> int:
        """Return how many tokens a document has.

        Args:
            doc_id: Identifier of an indexed document.

        Returns:
            The token count.

        Raises:
            KeyError: If the document is not in the index.
        """
        return self._doc_lengths[doc_id]

    def doc_freq(self, term: str) -> int:
        """Return in how many documents a term appears (0 if unknown).

        This counts documents, not occurrences: "laplace" appearing twice in
        one document still adds only 1.

        Args:
            term: A token.

        Returns:
            The number of documents containing the term.
        """
        return len(self._postings.get(term, ()))

    def postings(self, term: str) -> Mapping[int, int]:
        """Return a read-only view of ``{doc_id: term frequency}`` for a term.

        The view is read-only (``MappingProxyType``), so callers cannot change
        the index by accident.

        Args:
            term: A token.

        Returns:
            Document ids mapped to how often the term occurs in each; empty if
            the term is unknown.
        """
        return MappingProxyType(self._postings.get(term, {}))
