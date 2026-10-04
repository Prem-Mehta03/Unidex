import math

import pytest

from unidex.search.bm25 import Bm25Scorer
from unidex.search.inverted_index import InvertedIndex

K1 = 1.5
B = 0.75


def build() -> InvertedIndex:
    index = InvertedIndex()
    index.add_document(0, ["laplace", "transform", "guide"])
    index.add_document(1, ["fourier", "series", "guide"])
    index.add_document(2, ["laplace", "laplace", "note", "pdf", "pdf"])
    index.add_document(3, ["pdf", "pdf", "pdf", "scan"])
    return index


def expected_idf(n: int, total: int) -> float:
    return math.log(1 + (total - n + 0.5) / (n + 0.5))


def expected_term_score(tf: int, doc_len: int, avg_len: float, n: int, total: int) -> float:
    norm = 1 - B + B * doc_len / avg_len
    return expected_idf(n, total) * tf * (K1 + 1) / (tf + K1 * norm)


def test_idf_matches_formula_and_rare_terms_weigh_more() -> None:
    scorer = Bm25Scorer(build())
    assert scorer.idf("laplace") == pytest.approx(expected_idf(2, 4))
    assert scorer.idf("fourier") == pytest.approx(expected_idf(1, 4))
    assert scorer.idf("fourier") > scorer.idf("laplace") > scorer.idf("pdf") * 0.5
    assert scorer.idf("missing") == pytest.approx(expected_idf(0, 4))


def test_idf_is_never_negative() -> None:
    index = InvertedIndex()
    for doc_id in range(5):
        index.add_document(doc_id, ["everywhere"])
    assert Bm25Scorer(index).idf("everywhere") > 0


def test_scores_match_hand_computed_values() -> None:
    index = build()
    avg = index.avg_doc_length
    hits = Bm25Scorer(index, k1=K1, b=B).search(["laplace", "guide"], limit=10)
    scores = {hit.doc_id: hit.score for hit in hits}

    laplace_n, guide_n, total = 2, 2, 4
    assert scores[0] == pytest.approx(
        expected_term_score(1, 3, avg, laplace_n, total)
        + expected_term_score(1, 3, avg, guide_n, total)
    )
    assert scores[1] == pytest.approx(expected_term_score(1, 3, avg, guide_n, total))
    assert scores[2] == pytest.approx(expected_term_score(2, 5, avg, laplace_n, total))
    assert 3 not in scores


def test_results_are_sorted_best_first() -> None:
    hits = Bm25Scorer(build()).search(["laplace", "guide"])
    assert hits[0].doc_id == 0
    scores = [hit.score for hit in hits]
    assert scores == sorted(scores, reverse=True)


def test_rare_term_beats_common_term() -> None:
    index = InvertedIndex()
    index.add_document(0, ["laplace"])
    index.add_document(1, ["pdf"])
    index.add_document(2, ["pdf"])
    index.add_document(3, ["pdf"])
    index.add_document(4, ["pdf", "pdf", "pdf"])
    hits = Bm25Scorer(index).search(["laplace", "pdf"])
    # Doc 4 repeats "pdf" three times, but "laplace" is far rarer, so doc 0 still wins.
    assert hits[0].doc_id == 0


def test_repeated_query_terms_count_once() -> None:
    scorer = Bm25Scorer(build())
    once = scorer.search(["laplace"])
    thrice = scorer.search(["laplace", "laplace", "laplace"])
    assert [(h.doc_id, h.score) for h in once] == [(h.doc_id, h.score) for h in thrice]


def test_unknown_terms_and_empty_queries_return_nothing() -> None:
    scorer = Bm25Scorer(build())
    assert scorer.search(["nonexistent"]) == []
    assert scorer.search([]) == []


def test_limit_is_respected_and_non_positive_limit_returns_nothing() -> None:
    scorer = Bm25Scorer(build())
    assert len(scorer.search(["guide", "laplace", "pdf"], limit=2)) == 2
    assert scorer.search(["guide"], limit=0) == []
    assert scorer.search(["guide"], limit=-3) == []


def test_ties_are_broken_by_lower_document_id() -> None:
    index = InvertedIndex()
    index.add_document(5, ["same", "words"])
    index.add_document(2, ["same", "words"])
    index.add_document(9, ["same", "words"])
    hits = Bm25Scorer(index).search(["same"])
    assert [hit.doc_id for hit in hits] == [2, 5, 9]


def test_allowed_ids_restrict_results() -> None:
    hits = Bm25Scorer(build()).search(["laplace", "guide"], allowed_ids={1, 2})
    assert {hit.doc_id for hit in hits} == {1, 2}


def test_shorter_document_wins_when_term_frequency_is_equal() -> None:
    index = InvertedIndex()
    index.add_document(0, ["laplace"] + ["filler"] * 20)
    index.add_document(1, ["laplace", "filler"])
    hits = Bm25Scorer(index).search(["laplace"])
    assert [hit.doc_id for hit in hits] == [1, 0]


def test_empty_index_returns_nothing() -> None:
    assert Bm25Scorer(InvertedIndex()).search(["anything"]) == []
