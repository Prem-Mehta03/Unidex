from pathlib import Path

import pytest

from unidex.exceptions import IngestionError
from unidex.models.search import SearchDocument
from unidex.search.evaluation import EvalQuery, hit_at_k, is_relevant, load_queries
from unidex.search.strategies import Bm25Search

DOCS = [
    SearchDocument(0, "Midsem Solutions.pdf", "/M3/Sem 1 25-26/Evals", "u0"),
    SearchDocument(1, "Midsem QP.pdf", "/DD/Sem 1 25-26/Midsem", "u1"),
]


def test_is_relevant_checks_name_and_path_case_insensitively() -> None:
    query = EvalQuery("q", "midsem solution", "m3")
    assert is_relevant(DOCS[0], query)
    assert not is_relevant(DOCS[1], query)


def test_empty_expectations_match_anything() -> None:
    assert is_relevant(DOCS[1], EvalQuery("q", "", ""))
    assert is_relevant(DOCS[1], EvalQuery("q", "", "/dd/"))


def test_hit_at_k_looks_only_at_top_k() -> None:
    strategy = Bm25Search(DOCS)
    by_id = {d.doc_id: d for d in DOCS}
    assert hit_at_k(strategy, by_id, EvalQuery("m3 midsem solutions", "solution", "m3"), k=1)
    assert not hit_at_k(strategy, by_id, EvalQuery("quantum", "solution", ""), k=5)


def test_load_queries_reads_rows_and_skips_blank_queries(tmp_path: Path) -> None:
    path = tmp_path / "q.csv"
    path.write_text(
        "query,must_contain_name,must_contain_path,note\n"
        "laplace guide,laplace,Topicwise,first\n"
        ",ignored,,blank\n",
        encoding="utf-8",
    )
    assert load_queries(path) == [EvalQuery("laplace guide", "laplace", "Topicwise")]


def test_load_queries_rejects_bad_files(tmp_path: Path) -> None:
    with pytest.raises(IngestionError):
        load_queries(tmp_path / "missing.csv")
    bad = tmp_path / "bad.csv"
    bad.write_text("query\nx\n", encoding="utf-8")
    with pytest.raises(IngestionError):
        load_queries(bad)
