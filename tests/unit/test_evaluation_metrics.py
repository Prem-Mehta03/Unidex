import pytest

from unidex.models.search import SearchDocument
from unidex.search.evaluation import EvalQuery, first_relevant_rank, reciprocal_rank
from unidex.search.strategies import Bm25Search

# For the query "midsem solutions", document 0 matches both words and document 1
# matches only "midsem", so BM25 ranks 0 first and 1 second.
DOCS = [
    SearchDocument(0, "Midsem Solutions.pdf", "/M3/Evals", "u0"),
    SearchDocument(1, "Midsem QP.pdf", "/M3/Evals", "u1"),
]
BY_ID = {d.doc_id: d for d in DOCS}
QUERY_TEXT = "midsem solutions"


def test_first_relevant_rank_is_one_based() -> None:
    strategy = Bm25Search(DOCS)
    assert first_relevant_rank(strategy, BY_ID, EvalQuery(QUERY_TEXT, "solutions", ""), 5) == 1
    assert first_relevant_rank(strategy, BY_ID, EvalQuery(QUERY_TEXT, "midsem qp", ""), 5) == 2


def test_first_relevant_rank_is_none_outside_the_cutoff() -> None:
    strategy = Bm25Search(DOCS)
    assert first_relevant_rank(strategy, BY_ID, EvalQuery(QUERY_TEXT, "midsem qp", ""), 1) is None


def test_reciprocal_rank_values() -> None:
    strategy = Bm25Search(DOCS)
    first = EvalQuery(QUERY_TEXT, "solutions", "")
    second = EvalQuery(QUERY_TEXT, "midsem qp", "")
    missing = EvalQuery("quantum", "midsem qp", "")
    assert reciprocal_rank(strategy, BY_ID, first, 5) == 1.0
    assert reciprocal_rank(strategy, BY_ID, second, 5) == pytest.approx(0.5)
    assert reciprocal_rank(strategy, BY_ID, missing, 5) == 0.0
