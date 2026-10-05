"""Measure everything Unidex can honestly be measured on, and write ``docs/results.md``.

Usage (from the repo root, after syncing, extracting and reading contents)::

    python scripts/measure.py
    python scripts/measure.py --sample 300 --output docs/results.md

No language-model requests are made. Sections:

1. Collection: how many documents, by type, how they were labelled.
2. Contents: how many PDFs were read, by which method, and what share is searchable.
3. Name search: naive scan vs BM25 on ``eval/queries.csv`` (hit@1, hit@5, MRR).
4. Speed: median and 95th-percentile time per search, and growth with collection size.
5. Content search: a self-retrieval test on text read from inside files.
6. Chat: how often the rules read ``eval/chat_messages.csv`` correctly.
"""

import argparse
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path

from unidex.api.app import load_catalog
from unidex.chat.evaluation import FIELDS, evaluate, load_cases
from unidex.chat.service import ChatService
from unidex.config import load_settings
from unidex.content.quality import MIN_QUALITY
from unidex.db.connection import connect
from unidex.db.repositories import (
    DocumentView,
    RawFileRepository,
    iter_document_views,
    load_searchable_texts,
)
from unidex.exceptions import UnidexError
from unidex.logging_setup import configure_logging
from unidex.models.search import SearchDocument
from unidex.search.catalog import Catalog
from unidex.search.corpus import build_corpus
from unidex.search.evaluation import first_relevant_rank, load_queries
from unidex.search.measure import (
    RankStats,
    percentile,
    rank_stats,
    scale_corpus,
    self_queries,
)
from unidex.search.strategies import Bm25Search, NaiveSearch, SearchStrategy

logger = logging.getLogger("measure")

DEFAULT_OUTPUT = Path("docs/results.md")
DEFAULT_QUERIES = Path("eval/queries.csv")
DEFAULT_CHAT = Path("eval/chat_messages.csv")
RANK_CUTOFF = 10
TOP_K = 5
SCALE = 100
MILLISECONDS = 1000.0
REPEATS = 5


def time_each_ms(run: Callable[[str], object], queries: Sequence[str], repeats: int) -> list[float]:
    """Time every query ``repeats`` times and return the individual timings.

    Args:
        run: Function that performs one search.
        queries: Query texts.
        repeats: Repetitions of the whole list.

    Returns:
        One timing in milliseconds per search run.
    """
    for text in queries:  # warm-up, not timed
        run(text)
    timings: list[float] = []
    for _ in range(repeats):
        for text in queries:
            started = time.perf_counter()
            run(text)
            timings.append((time.perf_counter() - started) * MILLISECONDS)
    return timings


def _search(strategy: SearchStrategy, text: str) -> object:
    """Run one top-k search."""
    return strategy.search(text, limit=TOP_K)


def pct(part: int, whole: int) -> str:
    """Format ``part / whole`` as a percentage."""
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def table(header: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    """Build a Markdown table."""
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(str(c) for c in row) + " |" for row in rows)
    return [*lines, ""]


def collection_section(views: Sequence[DocumentView], facts: dict[str, float]) -> list[str]:
    """Describe the collection and how it was labelled."""
    by_type = Counter(v.doc_type for v in views)
    by_method = Counter(v.method for v in views)
    courses = {v.course_code for v in views if v.course_code}
    total = len(views)
    labelled = sum(1 for v in views if v.course_code)
    confident = sum(1 for v in views if v.confidence >= 0.8)
    facts.update(documents=total, courses=len(courses), llm_labelled=by_method.get("llm", 0))
    facts["confident_share"] = 100 * confident / total if total else 0
    lines = [
        "## 1. Collection",
        "",
        f"{total} documents across {len(courses)} courses; {pct(labelled, total)} have a course, "
        f"{pct(confident, total)} were labelled with confidence 0.8 or more. Labelled by rules: "
        f"{by_method.get('rule', 0)}, by the language model: {by_method.get('llm', 0)}, "
        f"by manual review: {by_method.get('manual', 0)}.",
        "",
    ]
    lines += table(
        ["Document type", "Files"],
        [[t, n] for t, n in sorted(by_type.items(), key=lambda x: -x[1])],
    )
    return lines


def contents_section(
    conn_rows: list[tuple[str, str, int]], searchable: int, facts: dict[str, float]
) -> list[str]:
    """Describe how many PDFs were read and with which method."""
    total = sum(n for _, _, n in conn_rows)
    by_method: Counter[str] = Counter()
    by_type: dict[str, Counter[str]] = {}
    for doc_type, method, n in conn_rows:
        by_method[method] += n
        by_type.setdefault(doc_type, Counter())[method] += n
    facts.update(pdfs_read=total, searchable=searchable, ocr=by_method["ocr"])
    lines = [
        "## 2. Reading file contents",
        "",
        f"{total} PDFs were read. {by_method['pdf_text']} came from the PDF text layer, "
        f"{by_method['ocr']} through OCR and {by_method['none']} could not be read well enough "
        f"(handwriting, poor scans). **{searchable} ({pct(searchable, total)}) are searchable "
        f"by topic.**",
        "",
    ]
    rows = []
    for doc_type, counts in sorted(by_type.items()):
        all_n = sum(counts.values())
        readable = counts["pdf_text"] + counts["ocr"]
        rows.append(
            [
                doc_type,
                all_n,
                counts["pdf_text"],
                counts["ocr"],
                counts["none"],
                pct(readable, all_n),
            ]
        )
    lines += table(["Type", "PDFs read", "Text layer", "OCR", "Unreadable", "Searchable"], rows)
    return lines


def name_search_section(
    corpus: Sequence[SearchDocument], queries_path: Path, facts: dict[str, float]
) -> list[str]:
    """Compare naive scan and BM25 on the hand-written queries."""
    queries = load_queries(queries_path)
    by_id = {d.doc_id: d for d in corpus}
    strategies: dict[str, SearchStrategy] = {
        "naive": NaiveSearch(corpus),
        "bm25": Bm25Search(corpus),
    }
    stats: dict[str, RankStats] = {}
    rows = []
    for name, strategy in strategies.items():
        ranks = [first_relevant_rank(strategy, by_id, q, RANK_CUTOFF) for q in queries]
        stats[name] = rank_stats(ranks, TOP_K)
        s = stats[name]
        rows.append([name, f"{s.hit_at_1}/{s.total}", f"{s.hit_at_k}/{s.total}", f"{s.mrr:.3f}"])
    facts.update(
        name_queries=len(queries),
        bm25_hit_k=stats["bm25"].hit_at_k,
        bm25_mrr=stats["bm25"].mrr,
        naive_mrr=stats["naive"].mrr,
    )
    lines = [
        "## 3. Search by file and folder name",
        "",
        f"{len(queries)} queries written by hand in `{queries_path}` (a file counts as right when "
        "its name and folder contain the expected text).",
        "",
    ]
    lines += table(["Strategy", "Right file first", f"In top {TOP_K}", "MRR@10"], rows)
    return lines


def speed_section(
    catalog: Catalog,
    corpus: Sequence[SearchDocument],
    queries_path: Path,
    facts: dict[str, float],
) -> list[str]:
    """Time searches on the real collection and on enlarged copies."""
    texts = [q.text for q in load_queries(queries_path)]
    timings = time_each_ms(lambda t: catalog.search(t), texts, REPEATS)
    facts.update(search_p50=percentile(timings, 50), search_p95=percentile(timings, 95))
    lines = [
        "## 4. Speed",
        "",
        f"Full catalog search (ranking, filters and facet counts), {len(timings)} runs on "
        f"{len(catalog)} documents: median {percentile(timings, 50):.2f} ms, "
        f"95th percentile {percentile(timings, 95):.2f} ms.",
        "",
    ]
    rows = []
    for factor in (1, SCALE):
        big = scale_corpus(corpus, factor)
        row: list[float] = [len(big)]
        for factory in (NaiveSearch, Bm25Search):
            started = time.perf_counter()
            strategy = factory(big)
            build_ms = (time.perf_counter() - started) * MILLISECONDS
            each = time_each_ms(partial(_search, strategy), texts, 3)
            row += [percentile(each, 50), build_ms]
        rows.append(
            [f"{row[0]:.0f}", f"{row[1]:.3f}", f"{row[2]:.0f}", f"{row[3]:.3f}", f"{row[4]:.0f}"]
        )
        if factor == SCALE:
            facts.update(scaled_docs=len(big), naive_big=row[1], bm25_big=row[3])
    lines += [
        "Median time per search on enlarged collections (every file repeated):",
        "",
        *table(
            ["Documents", "Naive scan (ms)", "Naive build (ms)", "BM25 (ms)", "BM25 build (ms)"],
            rows,
        ),
    ]
    return lines


def content_section(
    catalog: Catalog,
    texts: dict[str, str],
    views: Sequence[DocumentView],
    sample: int,
    facts: dict[str, float],
) -> list[str]:
    """Self-retrieval test for the content index."""
    names = {v.drive_file_id: v.name for v in views}
    cases = self_queries(texts, names, sample=sample)
    ranks: list[int | None] = []
    timings: list[float] = []
    for case in cases:
        started = time.perf_counter()
        result = catalog.search_content(case.query, max_hits=RANK_CUTOFF)
        timings.append((time.perf_counter() - started) * MILLISECONDS)
        ids = [h.doc.drive_file_id for h in result.matched]
        ranks.append(ids.index(case.drive_file_id) + 1 if case.drive_file_id in ids else None)
    stats = rank_stats(ranks, TOP_K)
    facts.update(self_queries=stats.total, self_hit_k=stats.hit_at_k, self_mrr=stats.mrr)
    lines = ["## 5. Search inside file contents", ""]
    if not cases:
        return [*lines, "Not enough readable text to run the test.", ""]
    lines += [
        f"Self-retrieval test: {stats.total} queries were generated from {min(sample, len(texts))} "
        "randomly drawn readable files (files without enough rare words are skipped); each uses "
        "three words that are rare across the collection but repeated in that file (and not in "
        "its name). This shows the index finds what it holds; it is an upper "
        "bound, not a measure of how students phrase topics.",
        "",
        *table(
            ["Right file first", f"In top {TOP_K}", "MRR@10", "Median search (ms)"],
            [
                [
                    f"{pct(stats.hit_at_1, stats.total)} ({stats.hit_at_1}/{stats.total})",
                    f"{pct(stats.hit_at_k, stats.total)} ({stats.hit_at_k}/{stats.total})",
                    f"{stats.mrr:.3f}",
                    f"{percentile(timings, 50):.2f}",
                ]
            ],
        ),
    ]
    return lines


def chat_section(service: ChatService, path: Path, facts: dict[str, float]) -> list[str]:
    """Score the chat's message reading."""
    report = evaluate(load_cases(path), service.reply)
    asked, should = report.clarifications
    facts.update(chat_total=report.total, chat_forms=report.forms_correct)
    rows = [[name, f"{report.field_correct(name)}/{report.total}"] for name in FIELDS]
    lines = [
        "## 6. Chat: reading a message",
        "",
        f"{report.total} messages in `{path}`, read by rules only. Whole form correct: "
        f"{report.forms_correct}/{report.total} ({pct(report.forms_correct, report.total)}); "
        f"reply kind correct: {report.kinds_correct}/{report.total}; asked for the course when it "
        f"should: {asked}/{should}.",
        "",
        *table(["Field", "Correct"], rows),
        "**Caveat:** the messages were written by the developer and the rules were tuned after "
        "seeing the first run, so this is not a held-out score. Add real student messages to "
        "the file and re-run before quoting it.",
        "",
    ]
    return lines


def resume_section(f: dict[str, float]) -> list[str]:
    """Draft resume lines using only the numbers measured above."""
    lines = [
        "## 7. Resume draft (numbers come from this report)",
        "",
        "**Unidex: unified search over college Google Drives** "
        "(Python, FastAPI, SQLite, BM25, Google OAuth)",
        "",
        f"- Built a chat-first search service over {f['documents']:.0f} files from "
        f"{f['courses']:.0f} department course drives, grouping past papers, solutions, notes and "
        "slides into stacks that link to the original Drive files (nothing is copied).",
        "- Implemented an inverted index, BM25 ranking, a trie for autocomplete and filter indexes "
        f"from scratch; on {f['name_queries']:.0f} hand-written queries BM25 put the right file in "
        f"the top 5 for {f['bm25_hit_k']:.0f}, with MRR {f['bm25_mrr']:.2f} against "
        f"{f['naive_mrr']:.2f} for a naive scan; median search time {f['search_p50']:.1f} ms "
        f"(p95 {f['search_p95']:.1f} ms).",
        "- Designed a normalised SQLite schema (raw Drive listing vs derived documents, idempotent "
        "upserts, resumable Drive sync that hides vanished files) with a rules-first extractor "
        f"that labels {f['confident_share']:.0f}% of documents with confidence 0.8 or more without "
        "a language-model request.",
    ]
    if "pdfs_read" in f:
        lines.append(
            f"- Read {f['pdfs_read']:.0f} PDFs (text layer, then OCR for scans, with a "
            f"text-quality gate for handwriting) so papers can be searched by topic; "
            f"{100 * f['searchable'] / f['pdfs_read']:.0f}% are searchable."
        )
    lines += [
        "- Added Google sign-in restricted to the college domain (OIDC with PKCE), per-user rate "
        "limits, issue reports and privacy-preserving usage logs; free-tier Gemini is optional and "
        "budget-capped.",
        "",
        "Use only the lines you are comfortable defending in an interview; each number above is "
        "reproduced by `python scripts/measure.py`. The chat score and the content self-test have "
        "the caveats stated in their sections.",
        "",
    ]
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--chat", type=Path, default=DEFAULT_CHAT)
    parser.add_argument("--sample", type=int, default=200, help="files for the content test")
    args = parser.parse_args(argv)

    facts: dict[str, float] = {}
    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        conn = connect(settings.db_path)
        try:
            views = list(iter_document_views(conn))
            corpus = build_corpus(RawFileRepository(conn).iter_all())
            texts = load_searchable_texts(conn, MIN_QUALITY)
            read_rows = [
                (str(r["doc_type"]), str(r["method"]), int(r["n"]))
                for r in conn.execute(
                    """
                    SELECT d.doc_type AS doc_type, t.method AS method, COUNT(*) AS n
                    FROM document_text t JOIN documents d ON d.id = t.document_id
                    WHERE d.link_status != 'broken' GROUP BY d.doc_type, t.method
                    """
                )
            ]
        finally:
            conn.close()
        catalog = load_catalog(settings)
        sections = [
            "# Unidex measurements",
            "",
            f"Generated by `scripts/measure.py` on {time.strftime('%Y-%m-%d')}. "
            "No language-model requests were used for any number below.",
            "",
            *collection_section(views, facts),
        ]
        if read_rows:
            sections += contents_section(read_rows, len(texts), facts)
        sections += name_search_section(corpus, args.queries, facts)
        sections += speed_section(catalog, corpus, args.queries, facts)
        if texts:
            sections += content_section(catalog, texts, views, args.sample, facts)
        sections += chat_section(ChatService(catalog), args.chat, facts)
        sections += resume_section(facts)
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(sections), encoding="utf-8")
    logger.info("Wrote %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
