from unidex.models.raw_file import RawFile
from unidex.models.search import SearchDocument
from unidex.search.corpus import build_corpus
from unidex.search.strategies import Bm25Search, NaiveSearch, SearchStrategy


def doc(doc_id: int, title: str, path: str = "/course") -> SearchDocument:
    return SearchDocument(doc_id=doc_id, title=title, path=path, url=f"https://x/{doc_id}")


def corpus() -> list[SearchDocument]:
    return [
        doc(0, "scans pdf pdf pdf.pdf", "/misc"),
        doc(1, "Laplace Transforms.pdf", "/M3/Topicwise Guides"),
        doc(2, "Fourier Series.pdf", "/M3/Topicwise Guides"),
        doc(3, "Midsem Solutions.pdf", "/M3/Sem 1 25-26/Evals"),
        doc(4, "Lecture06.pdf", "/M3/Slides"),
    ]


def ids(strategy: SearchStrategy, query: str, limit: int = 10) -> list[int]:
    return [hit.doc_id for hit in strategy.search(query, limit=limit)]


def test_both_strategies_find_obvious_matches() -> None:
    docs = corpus()
    for strategy in (NaiveSearch(docs), Bm25Search(docs)):
        assert ids(strategy, "fourier")[0] == 2
        assert ids(strategy, "midsem solutions")[0] == 3
        assert ids(strategy, "lecture 6")[0] == 4


def test_queries_are_normalised_like_documents() -> None:
    docs = corpus()
    for strategy in (NaiveSearch(docs), Bm25Search(docs)):
        assert ids(strategy, "LAPLACE transform")[0] == 1
        assert ids(strategy, "") == []
        assert ids(strategy, "of the") == []


def test_no_match_returns_nothing() -> None:
    docs = corpus()
    assert ids(NaiveSearch(docs), "quantum") == []
    assert ids(Bm25Search(docs), "quantum") == []


def test_limit_is_respected() -> None:
    docs = corpus()
    assert len(ids(Bm25Search(docs), "m3", limit=2)) == 2
    assert len(ids(NaiveSearch(docs), "pdf", limit=3)) == 3


def test_bm25_weights_rare_words_but_naive_counts_all_words_equally() -> None:
    docs = [
        doc(0, "pdf.pdf", "/x"),
        doc(1, "laplace.txt", "/x"),
        doc(2, "scan.pdf", "/x"),
        doc(3, "notes.pdf", "/x"),
    ]
    # Every document matches exactly one query word, so the naive scan sees a
    # four-way tie and falls back to the lower document id.
    assert ids(NaiveSearch(docs), "laplace pdf")[0] == 0
    # BM25 knows "laplace" is rare (1 document) and "pdf" is common (3 documents).
    assert ids(Bm25Search(docs), "laplace pdf")[0] == 1


def test_suggest_returns_real_words_most_common_first() -> None:
    search = Bm25Search(corpus())
    assert search.suggest("lap") == ["laplace"]
    assert search.suggest("M") == ["m3", "midsem", "misc"]
    assert search.suggest("sol") == ["solutions"]  # unstemmed, human-readable
    assert search.suggest("") == []
    assert search.suggest("  ") == []
    assert search.suggest("zzz") == []


def test_build_corpus_skips_non_indexable_and_assigns_consecutive_ids() -> None:
    def raw(name: str, indexable: bool) -> RawFile:
        return RawFile(name, "/p", name, "pdf", "m", None, f"https://x/{name}", indexable)

    docs = build_corpus([raw("a", True), raw("code.v", False), raw("b", True)])
    assert [(d.doc_id, d.title) for d in docs] == [(0, "a"), (1, "b")]


def test_allowed_ids_limits_both_strategies() -> None:
    docs = [
        SearchDocument(doc_id=i, title=f"laplace notes {i}", path="/x", url="u") for i in range(4)
    ]
    for strategy in (NaiveSearch(docs), Bm25Search(docs)):
        found = {hit.doc_id for hit in strategy.search("laplace", allowed_ids={1, 3})}
        assert found == {1, 3}
        assert strategy.search("laplace", allowed_ids=set()) == []
