"""Build the searchable corpus from stored files."""

from collections.abc import Iterable

from unidex.models.raw_file import RawFile
from unidex.models.search import SearchDocument


def build_corpus(files: Iterable[RawFile]) -> list[SearchDocument]:
    """Turn indexable raw files into search documents.

    Files flagged ``is_indexable = False`` (lab code, archives, ...) are
    skipped. Document ids are consecutive integers starting at 0, so a
    document can be fetched with ``corpus[doc_id]``.

    Args:
        files: Raw files, typically ``RawFileRepository.iter_all()``.

    Returns:
        One :class:`SearchDocument` per indexable file, in input order.
    """
    corpus: list[SearchDocument] = []
    for file in files:
        if not file.is_indexable:
            continue
        corpus.append(
            SearchDocument(doc_id=len(corpus), title=file.name, path=file.path, url=file.url)
        )
    return corpus
