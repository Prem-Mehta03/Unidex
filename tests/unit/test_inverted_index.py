import pytest

from unidex.search.inverted_index import InvertedIndex


def build() -> InvertedIndex:
    index = InvertedIndex()
    index.add_document(0, ["laplace", "transform", "guide"])
    index.add_document(1, ["fourier", "series", "guide"])
    index.add_document(2, ["laplace", "laplace", "note"])
    return index


def test_empty_index() -> None:
    index = InvertedIndex()
    assert index.num_docs == 0
    assert index.avg_doc_length == 0.0
    assert index.doc_freq("anything") == 0
    assert dict(index.postings("anything")) == {}


def test_postings_map_documents_to_term_frequency() -> None:
    index = build()
    assert dict(index.postings("laplace")) == {0: 1, 2: 2}
    assert dict(index.postings("guide")) == {0: 1, 1: 1}
    assert dict(index.postings("missing")) == {}


def test_document_frequency_counts_documents_not_occurrences() -> None:
    index = build()
    assert index.doc_freq("laplace") == 2
    assert index.doc_freq("fourier") == 1
    assert index.doc_freq("missing") == 0


def test_lengths_and_average() -> None:
    index = build()
    assert index.num_docs == 3
    assert index.doc_length(0) == 3
    assert index.doc_length(2) == 3
    assert index.avg_doc_length == pytest.approx(3.0)

    index.add_document(3, ["solo"])
    assert index.avg_doc_length == pytest.approx(10 / 4)


def test_unknown_document_length_raises() -> None:
    with pytest.raises(KeyError):
        build().doc_length(99)


def test_duplicate_document_id_is_rejected() -> None:
    index = build()
    with pytest.raises(ValueError, match="already"):
        index.add_document(0, ["again"])


def test_empty_document_is_counted_but_has_no_postings() -> None:
    index = InvertedIndex()
    index.add_document(7, [])
    assert index.num_docs == 1
    assert index.doc_length(7) == 0


def test_postings_view_cannot_modify_the_index() -> None:
    index = build()
    view = index.postings("laplace")
    with pytest.raises(TypeError):
        view[5] = 1  # type: ignore[index]
    assert index.doc_freq("laplace") == 2
