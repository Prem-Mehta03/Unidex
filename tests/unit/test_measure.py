import pytest

from unidex.models.search import SearchDocument
from unidex.search.measure import percentile, rank_stats, scale_corpus, self_queries


def test_percentile_interpolates() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5
    assert percentile([10, 20, 30], 0) == 10
    assert percentile([10, 20, 30], 100) == 30
    assert percentile([30, 10, 20], 50) == 20  # input order does not matter


@pytest.mark.parametrize(("values", "p"), [([], 50), ([1], -1), ([1], 101)])
def test_percentile_rejects_bad_input(values: list[float], p: float) -> None:
    with pytest.raises(ValueError, match=r"percentile|between"):
        percentile(values, p)


def test_rank_stats() -> None:
    stats = rank_stats([1, 2, None, 6, 1], k=5)
    assert (stats.total, stats.hit_at_1, stats.hit_at_k) == (5, 2, 3)
    assert stats.mrr == pytest.approx((1 + 0.5 + 0 + 1 / 6 + 1) / 5)
    assert rank_stats([], k=5).mrr == 0.0


def test_scale_corpus_makes_distinct_copies() -> None:
    docs = [SearchDocument(doc_id=0, title="a b", path="/x", url="u")]
    assert scale_corpus(docs, 1) == docs
    big = scale_corpus(docs, 3)
    assert [d.doc_id for d in big] == [0, 1, 2]
    assert len({d.title for d in big}) == 3


def make_texts() -> tuple[dict[str, str], dict[str, str]]:
    common = "algorithm complexity analysis " * 5
    texts = {
        "a": common + "kruskal spanning kruskal spanning boruvka boruvka",
        "b": common + "eigenvalue matrix eigenvalue matrix diagonal diagonal",
        "c": common + "semaphore deadlock semaphore deadlock mutex mutex",
    }
    for i in range(20):
        texts[f"f{i}"] = common
    names = {key: key for key in texts}
    return texts, names


def test_self_queries_use_rare_words_from_the_file() -> None:
    texts, names = make_texts()
    queries = {q.drive_file_id: q.query for q in self_queries(texts, names, sample=50)}
    assert set(queries) == {"a", "b", "c"}  # the filler files have no rare words
    assert set(queries["a"].split()) == {"kruskal", "spanning", "boruvka"}
    assert "algorithm" not in queries["a"]  # common words are not distinctive


def test_self_queries_skip_words_in_the_file_name() -> None:
    texts, names = make_texts()
    names["a"] = "Kruskal notes"
    queries = {q.drive_file_id: q.query for q in self_queries(texts, names, sample=50)}
    assert "a" not in queries  # only two rare words are left, fewer than three


def test_self_queries_are_repeatable_and_sampled() -> None:
    texts, names = make_texts()
    first = self_queries(texts, names, sample=2, seed=3)
    assert first == self_queries(texts, names, sample=2, seed=3)
    assert len(first) <= 2
