import pytest

from unidex.search.tokenizer import tokenize


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Midsem_Solution.pdf", ["midsem", "solution", "pdf"]),
        ("CS F213 16-17.pdf", ["cs", "f213", "16", "17", "pdf"]),
        ("Lecture06.pdf", ["lecture", "6", "pdf"]),
        ("Lecture 6", ["lecture", "6"]),
        ("MATH3-Lect13.pdf", ["math", "3", "lect", "13", "pdf"]),
        ("M3", ["m3"]),
        ("Quiz_1", ["quiz", "1"]),
        ("Sem 1 25-26 (Anupama Sharma)", ["sem", "1", "25", "26", "anupama", "sharma"]),
    ],
)
def test_tokenize_file_and_folder_names(text: str, expected: list[str]) -> None:
    assert tokenize(text) == expected


def test_plural_s_is_stripped_but_not_from_short_or_ss_words() -> None:
    assert tokenize("Slides Notes Solutions") == ["slide", "note", "solution"]
    assert tokenize("class lcs gas") == ["class", "lcs", "gas"]


def test_stopwords_are_dropped() -> None:
    assert tokenize("Logic in CS and the Notes of DSA") == ["logic", "cs", "note", "dsa"]


def test_stemming_can_be_turned_off() -> None:
    assert tokenize("Slides Series", stem=False) == ["slides", "series"]


def test_numbers_lose_leading_zeros_but_zero_survives() -> None:
    assert tokenize("007 000 10") == ["7", "0", "10"]


def test_accents_and_case_are_folded() -> None:
    assert tokenize("Café ÉCOLE") == ["cafe", "ecole"]


def test_duplicates_are_kept_and_empty_input_gives_nothing() -> None:
    assert tokenize("laplace laplace") == ["laplace", "laplace"]
    assert tokenize("") == []
    assert tokenize("   ---  ") == []


def test_same_text_always_gives_same_tokens() -> None:
    assert tokenize("M3 Midsem Solutions") == tokenize("m3 midsem solutions")
