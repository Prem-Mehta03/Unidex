"""Run extraction end to end: rules, optional language model, save, queue."""

import logging
import sqlite3
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from unidex.db.connection import transaction
from unidex.db.repositories import (
    CourseRepository,
    DocumentRepository,
    RawFileRepository,
    ReviewQueueRepository,
    StoredFile,
)
from unidex.extraction.llm_extractor import LlmClassifier, LlmVerdict
from unidex.extraction.rules import EXAM_RELEVANT, RuleExtractor
from unidex.models.enums import DocType, ExamType, ExtractionMethod
from unidex.models.metadata import ExtractedMetadata

logger = logging.getLogger(__name__)

# Confidence given to a language-model answer: usable for search, but it is
# always listed in the review queue until a human confirms it.
LLM_CONFIDENCE = 0.65


def _utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ExtractionReport:
    """What an extraction run did.

    Attributes:
        total: Searchable files processed.
        by_doc_type: Number of documents per document type.
        llm_candidates: Files the rules could not fully decide.
        llm_reused: Candidates answered from an earlier run (no request made).
        llm_requests: Language-model requests made in this run.
        llm_filled: Files whose metadata the model filled in.
        review_open: Files now waiting in the review queue.
    """

    total: int
    by_doc_type: dict[str, int]
    llm_candidates: int
    llm_reused: int
    llm_requests: int
    llm_filled: int
    review_open: int


def apply_verdict(meta: ExtractedMetadata, verdict: LlmVerdict) -> ExtractedMetadata:
    """Fill the gaps in rule output with a language-model answer.

    Rule results are never overwritten: the model may only supply a value the
    rules left ``UNKNOWN`` (or an exam number / make-up flag the rules did not
    find). If the model adds nothing, the metadata is returned unchanged.

    Args:
        meta: What the rules produced.
        verdict: The validated model answer.

    Returns:
        New metadata, labelled as coming from the model when it added anything.
    """
    doc_type = meta.doc_type
    if doc_type is DocType.UNKNOWN and verdict.doc_type is not DocType.UNKNOWN:
        doc_type = verdict.doc_type
    exam_type = meta.exam_type
    if exam_type is ExamType.UNKNOWN and verdict.exam_type is not ExamType.UNKNOWN:
        exam_type = verdict.exam_type
    if doc_type not in EXAM_RELEVANT and exam_type is ExamType.UNKNOWN:
        exam_type = ExamType.NONE
    exam_number = meta.exam_number
    if exam_number is None and exam_type in (ExamType.QUIZ, ExamType.TEST, ExamType.LAB_QUIZ):
        exam_number = verdict.exam_number
    is_makeup = meta.is_makeup or verdict.is_makeup

    changed = (
        doc_type is not meta.doc_type
        or exam_type is not meta.exam_type
        or exam_number != meta.exam_number
        or is_makeup != meta.is_makeup
    )
    if not changed:
        return meta
    return replace(
        meta,
        doc_type=doc_type,
        exam_type=exam_type,
        exam_number=exam_number,
        is_makeup=is_makeup,
        confidence=LLM_CONFIDENCE,
        method=ExtractionMethod.LLM,
        notes=(*meta.notes, "filled in by the language model"),
    )


class ExtractionPipeline:
    """Turns every searchable raw file into a ``documents`` row."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        classifier: LlmClassifier | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Configure a run.

        Args:
            conn: An open connection with the schema created.
            classifier: Language-model classifier, or ``None`` to use rules only.
            clock: Returns the current time; replaced in tests.
        """
        self._conn = conn
        self._classifier = classifier
        self._clock = clock

    def run(self) -> ExtractionReport:
        """Extract metadata for every searchable file and refresh the review queue.

        Rules run first. If a classifier is configured, files the rules could
        not fully decide are sent to it in batches; answers from earlier runs
        are reused without spending budget. Everything is then saved in one
        transaction, and rows a human already reviewed are left alone.

        Returns:
            Counts describing the run.
        """
        documents = DocumentRepository(self._conn)
        extractor = RuleExtractor(CourseRepository(self._conn).resolve)
        stored_files = list(RawFileRepository(self._conn).iter_indexable())
        results = [(stored, extractor.extract(stored.file)) for stored in stored_files]

        candidates = [i for i, (_, meta) in enumerate(results) if meta.needs_llm]
        reused = self._reuse_earlier_answers(documents, results, candidates)
        pending = [i for i in candidates if i not in reused]
        filled, requests = self._ask_model(results, pending)
        filled += len(reused)

        now = self._clock().isoformat(timespec="seconds")
        with transaction(self._conn):
            documents.upsert_many(results, now)
            ids = documents.ids_by_raw_file()
            entries = {
                ids[stored.raw_file_id][0]: "; ".join(meta.review_reasons)
                for stored, meta in results
                if meta.review_reasons and not ids[stored.raw_file_id][1]
            }
            queue = ReviewQueueRepository(self._conn)
            opened, resolved = queue.sync(entries, now)
            review_open = queue.count_open()

        report = ExtractionReport(
            total=len(results),
            by_doc_type=documents.count_by("doc_type"),
            llm_candidates=len(candidates),
            llm_reused=len(reused),
            llm_requests=requests,
            llm_filled=filled,
            review_open=review_open,
        )
        logger.info(
            "Extraction finished: %d files, %d need review (%d newly opened, %d resolved)",
            report.total,
            review_open,
            opened,
            resolved,
        )
        return report

    @staticmethod
    def _reuse_earlier_answers(
        documents: DocumentRepository,
        results: list[tuple[StoredFile, ExtractedMetadata]],
        candidates: list[int],
    ) -> set[int]:
        """Re-apply answers saved by earlier runs; return the indexes they covered."""
        earlier = documents.llm_answers()
        reused: set[int] = set()
        for index in candidates:
            stored, meta = results[index]
            saved = earlier.get(stored.raw_file_id)
            if saved is None:
                continue
            verdict = LlmVerdict(DocType(saved[0]), ExamType(saved[1]), saved[2], saved[3])
            updated = apply_verdict(meta, verdict)
            if updated is not meta:
                results[index] = (stored, updated)
                reused.add(index)
        return reused

    def _ask_model(
        self,
        results: list[tuple[StoredFile, ExtractedMetadata]],
        pending: list[int],
    ) -> tuple[int, int]:
        """Send undecided files to the model; return ``(filled, requests)``."""
        if self._classifier is None or not pending:
            return 0, 0
        files = [results[i][0].file for i in pending]
        verdicts, requests = self._classifier.classify(files)
        filled = 0
        for position, verdict in verdicts.items():
            index = pending[position]
            stored, meta = results[index]
            updated = apply_verdict(meta, verdict)
            if updated is not meta:
                results[index] = (stored, updated)
                filled += 1
        counts = Counter(meta.method.value for _, meta in results)
        logger.info("Metadata by method after language model: %s", dict(counts))
        return filled, requests
