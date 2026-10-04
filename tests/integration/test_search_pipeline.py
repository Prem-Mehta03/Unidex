import sqlite3

import pytest

from unidex.db.repositories import RawFileRepository
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.sync_job import SyncJob
from unidex.models.search import SearchDocument
from unidex.search.corpus import build_corpus
from unidex.search.strategies import Bm25Search, NaiveSearch

from ..conftest import SAMPLE_CSV, fixed_clock


@pytest.fixture
def corpus(conn: sqlite3.Connection) -> list[SearchDocument]:
    """Documents built from the sample CSV after a real sync into the database."""
    SyncJob(conn, CsvFileSource(SAMPLE_CSV), "CS", "CS archive", clock=fixed_clock).run()
    return build_corpus(RawFileRepository(conn).iter_all())


def top_title(strategy: Bm25Search | NaiveSearch, corpus: list[SearchDocument], query: str) -> str:
    hits = strategy.search(query, limit=1)
    return corpus[hits[0].doc_id].title


def test_code_files_never_enter_the_corpus(corpus: list[SearchDocument]) -> None:
    assert corpus
    assert not any(doc.title.endswith((".v", ".vcd", ".zip")) for doc in corpus)


def test_document_ids_index_the_corpus_list(corpus: list[SearchDocument]) -> None:
    assert [doc.doc_id for doc in corpus] == list(range(len(corpus)))


def test_bm25_finds_topic_guides(corpus: list[SearchDocument]) -> None:
    bm25 = Bm25Search(corpus)
    assert top_title(bm25, corpus, "laplace transforms") == "Laplace Transforms.pdf"
    assert top_title(bm25, corpus, "legendre") == "Legendre Polynomials.pdf"


def test_bm25_uses_folder_names_and_years(corpus: list[SearchDocument]) -> None:
    bm25 = Bm25Search(corpus)
    hits = bm25.search("dd compre", limit=1)
    assert corpus[hits[0].doc_id].path.startswith("/2-1 CDCs/DD")
    top = corpus[bm25.search("oop midsem 24-25", limit=1)[0].doc_id]
    assert "OOP" in top.path
    assert "24-25" in top.path
    assert "Midsem" in top.path


def test_plural_and_zero_padded_forms_match(corpus: list[SearchDocument]) -> None:
    bm25 = Bm25Search(corpus)
    titles = {corpus[hit.doc_id].title for hit in bm25.search("cheatsheets", limit=5)}
    assert "CompreCheatsheet1.pdf" in titles


def test_autocomplete_suggests_words_from_the_drive(corpus: list[SearchDocument]) -> None:
    suggestions = Bm25Search(corpus).suggest("lap")
    assert suggestions == ["laplace"]


def test_both_strategies_agree_on_unambiguous_queries(corpus: list[SearchDocument]) -> None:
    naive = NaiveSearch(corpus)
    bm25 = Bm25Search(corpus)
    assert top_title(naive, corpus, "bessel functions") == top_title(
        bm25, corpus, "bessel functions"
    )
