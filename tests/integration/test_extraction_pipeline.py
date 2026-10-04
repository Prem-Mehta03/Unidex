import json
import sqlite3

import pytest

from unidex.db.repositories import (
    DocumentRepository,
    LlmUsageRepository,
    ReviewQueueRepository,
)
from unidex.db.seed import seed_courses
from unidex.exceptions import LLMRateLimitError
from unidex.extraction.budget import LlmBudget
from unidex.extraction.llm_client import MockLLMClient
from unidex.extraction.llm_extractor import LlmClassifier, LlmVerdict
from unidex.extraction.pipeline import LLM_CONFIDENCE, ExtractionPipeline, apply_verdict
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.sync_job import SyncJob
from unidex.models.enums import DocType, ExamType, ExtractionMethod
from unidex.models.metadata import ExtractedMetadata

from ..conftest import ALIASES_CSV, SAMPLE_CSV, fixed_clock


@pytest.fixture
def loaded(conn: sqlite3.Connection) -> sqlite3.Connection:
    """A database with the sample drive loaded and courses seeded."""
    seed_courses(conn, ALIASES_CSV)
    SyncJob(conn, CsvFileSource(SAMPLE_CSV), "CS", "CS archive", clock=fixed_clock).run()
    return conn


def make_classifier(conn: sqlite3.Connection, client: MockLLMClient) -> LlmClassifier:
    budget = LlmBudget(LlmUsageRepository(conn), 100, 100, sleep=lambda _: None)
    return LlmClassifier(client, budget)


def doc_row(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    row = conn.execute(
        """
        SELECT d.* FROM documents d JOIN raw_files r ON r.id = d.raw_file_id WHERE r.name = ?
        """,
        (name,),
    ).fetchone()
    assert row is not None, name
    return row  # type: ignore[no-any-return]


def test_rules_only_run_creates_one_document_per_searchable_file(
    loaded: sqlite3.Connection,
) -> None:
    report = ExtractionPipeline(loaded, clock=fixed_clock).run()
    searchable = loaded.execute(
        "SELECT COUNT(*) AS n FROM raw_files WHERE is_indexable = 1"
    ).fetchone()
    assert report.total == searchable["n"] == DocumentRepository(loaded).count()
    assert report.llm_requests == 0


def test_known_sample_file_is_interpreted_correctly(loaded: sqlite3.Connection) -> None:
    ExtractionPipeline(loaded, clock=fixed_clock).run()
    row = doc_row(loaded, "Midsem 25-26 Solution.pdf")
    assert (row["doc_type"], row["exam_type"], row["academic_year"]) == ("solution", "midsem", 2025)
    assert row["instructor"] == "Prof. Iyer"
    assert row["has_solution"] == 1
    assert row["extraction_method"] == "rule"


def test_running_twice_gives_the_same_result(loaded: sqlite3.Connection) -> None:
    first = ExtractionPipeline(loaded, clock=fixed_clock).run()
    second = ExtractionPipeline(loaded, clock=fixed_clock).run()
    assert first == second
    assert DocumentRepository(loaded).count() == first.total


def test_review_queue_lists_only_uncertain_files(loaded: sqlite3.Connection) -> None:
    report = ExtractionPipeline(loaded, clock=fixed_clock).run()
    queue = ReviewQueueRepository(loaded)
    assert queue.count_open() == report.review_open
    assert report.review_open < report.total / 2


def test_model_fills_only_the_gaps_and_is_marked_for_review(loaded: sqlite3.Connection) -> None:
    ExtractionPipeline(loaded, clock=fixed_clock).run()
    unknown = loaded.execute(
        "SELECT r.name FROM documents d JOIN raw_files r ON r.id = d.raw_file_id "
        "WHERE d.doc_type = 'unknown'"
    ).fetchall()
    if not unknown:
        pytest.skip("sample data has no undecided files")
    answers = json.dumps(
        [
            {
                "id": i,
                "doc_type": "notes",
                "exam_type": "none",
                "exam_number": None,
                "is_makeup": False,
            }
            for i in range(len(unknown))
        ]
    )
    client = MockLLMClient(replies=[answers])
    report = ExtractionPipeline(loaded, make_classifier(loaded, client), clock=fixed_clock).run()
    assert report.llm_requests == 1
    assert report.llm_filled == len(unknown)
    row = doc_row(loaded, unknown[0]["name"])
    assert row["doc_type"] == "notes"
    assert row["extraction_method"] == "llm"
    assert row["extraction_confidence"] == LLM_CONFIDENCE
    assert ReviewQueueRepository(loaded).count_open() >= len(unknown)


def test_earlier_model_answers_are_reused_without_new_requests(loaded: sqlite3.Connection) -> None:
    ExtractionPipeline(loaded, clock=fixed_clock).run()
    count = loaded.execute(
        "SELECT COUNT(*) AS n FROM documents WHERE doc_type = 'unknown'"
    ).fetchone()["n"]
    if not count:
        pytest.skip("sample data has no undecided files")
    answers = json.dumps(
        [
            {
                "id": i,
                "doc_type": "notes",
                "exam_type": "none",
                "exam_number": None,
                "is_makeup": False,
            }
            for i in range(count)
        ]
    )
    first_client = MockLLMClient(replies=[answers])
    ExtractionPipeline(loaded, make_classifier(loaded, first_client), clock=fixed_clock).run()
    second_client = MockLLMClient(replies=[answers])
    report = ExtractionPipeline(
        loaded, make_classifier(loaded, second_client), clock=fixed_clock
    ).run()
    assert second_client.prompts == []
    assert report.llm_requests == 0
    assert report.llm_reused == count
    assert (
        loaded.execute("SELECT COUNT(*) AS n FROM documents WHERE doc_type = 'unknown'").fetchone()[
            "n"
        ]
        == 0
    )


def test_provider_failure_leaves_everything_in_the_review_queue(loaded: sqlite3.Connection) -> None:
    client = MockLLMClient(replies=["[]"], error=LLMRateLimitError("quota"))
    report = ExtractionPipeline(loaded, make_classifier(loaded, client), clock=fixed_clock).run()
    assert report.llm_filled == 0
    assert DocumentRepository(loaded).count() == report.total  # the run still completed


def test_rows_confirmed_by_a_human_are_not_overwritten(loaded: sqlite3.Connection) -> None:
    ExtractionPipeline(loaded, clock=fixed_clock).run()
    drive_id = loaded.execute(
        "SELECT drive_file_id FROM raw_files WHERE name = 'Quiz 1.pdf' LIMIT 1"
    ).fetchone()["drive_file_id"]
    assert DocumentRepository(loaded).mark_manual(
        drive_id, {"doc_type": "notes", "exam_type": "none"}
    )
    ExtractionPipeline(loaded, clock=fixed_clock).run()
    row = loaded.execute(
        "SELECT d.doc_type, d.extraction_method, d.reviewed FROM documents d "
        "JOIN raw_files r ON r.id = d.raw_file_id WHERE r.drive_file_id = ?",
        (drive_id,),
    ).fetchone()
    assert (row["doc_type"], row["extraction_method"], row["reviewed"]) == ("notes", "manual", 1)


def test_mark_manual_rejects_columns_that_are_not_editable(loaded: sqlite3.Connection) -> None:
    ExtractionPipeline(loaded, clock=fixed_clock).run()
    with pytest.raises(ValueError, match="not editable"):
        DocumentRepository(loaded).mark_manual("x", {"course_id": 3})
    with pytest.raises(ValueError, match="not editable"):
        DocumentRepository(loaded).mark_manual("x", {"doc_type = 'pyq' --": "x"})


def test_count_by_only_accepts_known_columns(loaded: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="Cannot group by"):
        DocumentRepository(loaded).count_by("title; DROP TABLE documents")


# --------------------------------------------------------------------- apply_verdict


def base_meta(**overrides: object) -> ExtractedMetadata:
    fields: dict[str, object] = {
        "course_id": 1,
        "academic_year": 2025,
        "semester": 1,
        "instructor": None,
        "exam_type": ExamType.UNKNOWN,
        "exam_number": None,
        "doc_type": DocType.UNKNOWN,
        "is_makeup": False,
        "has_solution": False,
        "title": "t",
        "confidence": 0.0,
    }
    fields.update(overrides)
    return ExtractedMetadata(**fields)  # type: ignore[arg-type]


def test_model_answer_never_overwrites_a_rule_result() -> None:
    meta = base_meta(doc_type=DocType.SLIDES, exam_type=ExamType.NONE, confidence=0.85)
    verdict = LlmVerdict(DocType.PYQ, ExamType.MIDSEM, 1, True)
    updated = apply_verdict(meta, verdict)
    assert updated.doc_type is DocType.SLIDES
    assert updated.exam_type is ExamType.NONE
    assert updated.is_makeup  # a make-up flag the rules missed may be added


def test_model_filling_nothing_returns_the_original_object() -> None:
    meta = base_meta()
    assert apply_verdict(meta, LlmVerdict(DocType.UNKNOWN, ExamType.UNKNOWN, None, False)) is meta


def test_study_material_from_the_model_gets_no_exam() -> None:
    updated = apply_verdict(base_meta(), LlmVerdict(DocType.NOTES, ExamType.UNKNOWN, None, False))
    assert (updated.doc_type, updated.exam_type) == (DocType.NOTES, ExamType.NONE)
    assert updated.method is ExtractionMethod.LLM
    assert updated.confidence == LLM_CONFIDENCE


def test_exam_number_is_taken_only_for_numbered_exams() -> None:
    quiz = apply_verdict(base_meta(), LlmVerdict(DocType.PYQ, ExamType.QUIZ, 3, False))
    midsem = apply_verdict(base_meta(), LlmVerdict(DocType.PYQ, ExamType.MIDSEM, 3, False))
    assert quiz.exam_number == 3
    assert midsem.exam_number is None
