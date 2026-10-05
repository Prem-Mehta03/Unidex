"""Check how many of your PDFs have real text inside versus being scans.

This decides how papers can be searched by topic later: text PDFs can be read directly,
scanned ones would need OCR. It downloads a small random sample of PDFs from Drive (read-only),
looks at them in memory, and keeps nothing except a summary table and a CSV.

Usage (from the project folder; needs ``pip install -e ".[content]"`` and a Drive sign-in)::

    python scripts/probe_pdfs.py                    # 12 PDFs of each type
    python scripts/probe_pdfs.py --per-type 25 --seed 7

The report is also saved to data/real/probe_pdfs.csv (git-ignored). The snippets in it are the
first words of each file, so do not paste that CSV anywhere public.
"""

import argparse
import csv
import logging
import random
import shutil
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path

from unidex.auth.drive import DriveCredentials, load_refresh_token
from unidex.config import load_settings
from unidex.content.probe import KIND_ERROR, PdfProbe, probe_pdf
from unidex.db.connection import connect
from unidex.db.repositories import FileKindRow, list_file_kinds
from unidex.exceptions import ConfigError, UnidexError
from unidex.ingestion.drive_download import DriveDownloader
from unidex.logging_setup import configure_logging

logger = logging.getLogger("probe_pdfs")

DEFAULT_PER_TYPE = 12
DEFAULT_OUTPUT = Path("data/real/probe_pdfs.csv")
DEFAULT_TYPES = ("pyq", "solution", "notes", "slides", "tutorial")
KINDS = ("text", "mixed", "scanned", "encrypted", "error")


def pick_sample(
    rows: Sequence[FileKindRow], types: Sequence[str], per_type: int, seed: int
) -> list[FileKindRow]:
    """Choose a reproducible random sample of PDFs for each document type.

    Args:
        rows: All documents.
        types: Document types to sample.
        per_type: How many PDFs to take per type (fewer if there are fewer).
        seed: Seed so the same sample can be repeated.

    Returns:
        The chosen rows.
    """
    rng = random.Random(seed)  # noqa: S311 - a sample, not security
    by_type: dict[str, list[FileKindRow]] = defaultdict(list)
    for row in rows:
        if row.extension == "pdf" and row.doc_type in types:
            by_type[row.doc_type].append(row)
    chosen: list[FileKindRow] = []
    for doc_type in types:
        pool = by_type.get(doc_type, [])
        chosen.extend(rng.sample(pool, min(per_type, len(pool))))
    return chosen


def log_inventory(rows: Sequence[FileKindRow]) -> None:
    """Log how many documents of each type are PDFs, slides decks and so on."""
    table: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        table[row.doc_type][row.extension or "(none)"] += 1
    logger.info("File types per document type:")
    for doc_type, counts in sorted(table.items()):
        parts = ", ".join(f"{ext} {n}" for ext, n in counts.most_common(5))
        logger.info("  %-10s %5d files  (%s)", doc_type, sum(counts.values()), parts)


def log_summary(results: Sequence[tuple[FileKindRow, PdfProbe]]) -> None:
    """Log text / scanned counts per document type."""
    by_type: dict[str, list[PdfProbe]] = defaultdict(list)
    for row, probe in results:
        by_type[row.doc_type].append(probe)
    logger.info("Sampled PDFs: how many have a real text layer?")
    for doc_type, probes in by_type.items():
        kinds = Counter(p.kind for p in probes)
        readable = [p.chars_per_page for p in probes if p.kind != KIND_ERROR]
        median = statistics.median(readable) if readable else 0.0
        counts = ", ".join(f"{k} {kinds[k]}" for k in KINDS if kinds[k])
        logger.info(
            "  %-10s %3d sampled: %s; median %.0f characters/page",
            doc_type,
            len(probes),
            counts,
            median,
        )


def write_csv(path: Path, results: Sequence[tuple[FileKindRow, PdfProbe]]) -> None:
    """Save one line per sampled file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["name", "course", "doc_type", "kind", "pages", "pages_with_text", "chars", "snippet"]
        )
        for row, probe in results:
            writer.writerow(
                [
                    row.name,
                    row.course_code,
                    row.doc_type,
                    probe.kind,
                    probe.pages,
                    probe.pages_with_text,
                    probe.chars,
                    probe.snippet,
                ]
            )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the script.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        Process exit code (0 on success, 1 on a handled error).
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    parser.add_argument("--per-type", type=int, default=DEFAULT_PER_TYPE)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--types", nargs="+", default=list(DEFAULT_TYPES))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
        configure_logging(settings.log_level)
        if not (settings.drive_client_id and settings.drive_client_secret):
            raise ConfigError("Set DRIVE_CLIENT_ID and DRIVE_CLIENT_SECRET in .env first")
        conn = connect(settings.db_path)
        try:
            rows = list_file_kinds(conn)
        finally:
            conn.close()
        log_inventory(rows)
        sample = pick_sample(rows, args.types, args.per_type, args.seed)
        logger.info("Downloading %d PDFs to look inside (nothing is saved)...", len(sample))
        credentials = DriveCredentials(
            settings.drive_client_id,
            settings.drive_client_secret,
            load_refresh_token(settings.drive_token_path),
        )
        downloader = DriveDownloader(credentials.access_token)
        results: list[tuple[FileKindRow, PdfProbe]] = []
        for index, row in enumerate(sample, start=1):
            try:
                probe = probe_pdf(downloader.download(row.drive_file_id))
            except UnidexError as exc:
                if isinstance(exc, ConfigError):
                    raise
                logger.warning("Skipping %s: %s", row.name, exc)
                probe = PdfProbe(kind=KIND_ERROR)
            results.append((row, probe))
            if index % 10 == 0:
                logger.info("  %d / %d done", index, len(sample))
    except UnidexError as exc:
        logger.error("%s", exc)
        return 1
    log_summary(results)
    write_csv(args.output, results)
    ocr = shutil.which("tesseract")
    logger.info("OCR program (tesseract) on this computer: %s", ocr or "not installed")
    logger.info("Saved details to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
