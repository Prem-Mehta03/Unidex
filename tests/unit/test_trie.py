import pytest

from unidex.search.trie import Trie


def build() -> Trie:
    trie = Trie()
    trie.insert("laplace", 5)
    trie.insert("lagrange", 2)
    trie.insert("lab", 9)
    trie.insert("lecture", 7)
    trie.insert("fourier", 4)
    return trie


def test_contains_only_whole_words() -> None:
    trie = build()
    assert "laplace" in trie
    assert "lap" not in trie
    assert "laplaces" not in trie
    assert "" not in trie


def test_len_counts_distinct_words() -> None:
    trie = build()
    assert len(trie) == 5
    trie.insert("laplace")
    assert len(trie) == 5


def test_complete_orders_by_weight_then_alphabet() -> None:
    assert build().complete("la") == ["lab", "laplace", "lagrange"]


def test_complete_with_equal_weights_is_alphabetical() -> None:
    trie = Trie()
    for word in ["bessel", "beta", "bell"]:
        trie.insert(word)
    assert trie.complete("be") == ["bell", "bessel", "beta"]


def test_prefix_that_is_itself_a_word_is_included() -> None:
    trie = Trie()
    trie.insert("lab", 1)
    trie.insert("labs", 3)
    trie.insert("laboratory", 2)
    assert trie.complete("lab") == ["labs", "laboratory", "lab"]


def test_limit_is_respected() -> None:
    assert build().complete("l", limit=2) == ["lab", "lecture"]
    assert build().complete("l", limit=0) == []
    assert build().complete("l", limit=-1) == []


def test_unknown_prefix_gives_nothing() -> None:
    assert build().complete("zzz") == []


def test_empty_prefix_returns_heaviest_words_overall() -> None:
    assert build().complete("", limit=3) == ["lab", "lecture", "laplace"]


def test_inserting_again_adds_weight() -> None:
    trie = Trie()
    trie.insert("alpha", 1)
    trie.insert("alpine", 2)
    assert trie.complete("al") == ["alpine", "alpha"]
    trie.insert("alpha", 5)
    assert trie.complete("al") == ["alpha", "alpine"]


def test_empty_word_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        Trie().insert("")


def test_empty_trie() -> None:
    trie = Trie()
    assert len(trie) == 0
    assert trie.complete("a") == []
    assert trie.complete("") == []
