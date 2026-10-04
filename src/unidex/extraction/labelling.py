"""Measure extraction accuracy against a human-checked sample.

A sheet is built from a sample spread evenly across document types (so the
many slide decks do not crowd out the rare handouts and cheat sheets) plus some
of the hardest documents. The checker marks each row ``y`` (the prediction is right) or ``n`` (and
fills the ``fix_*`` columns). Accuracy is then computed per field.

Be honest about what this measures: the random rows estimate accuracy on a
typical file; the hard rows (lowest confidence) are deliberately unrepresentative
and are reported separately. A checker who only reads file names, as the rules
do, will agree with the rules too often; open the actual file for at least some
rows, especially the year.
"""

import csv
import logging
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from unidex.db.repositories import DocumentView
from unidex.exceptions import ExtractionError

logger = logging.getLogger(__name__)

FIELDS = ("doc_type", "exam_type", "exam_number", "academic_year", "is_makeup")
SHEET_COLUMNS = (
    "stratum",
    "drive_file_id",
    "course",
    "path",
    "name",
    "url",
    *(f"pred_{name}" for name in FIELDS),
    "correct",
    *(f"fix_{name}" for name in FIELDS),
    "comment",
)
SPREAD = "spread"
HARD = "hard"
DEFAULT_SIZE = 100
DEFAULT_HARD_SHARE = 0.3


def _cell(value: object) -> str:
    """Format a value as CSV cell text (blank for ``None``, yes/no for booleans)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _balanced_sample(views: Sequence[DocumentView], count: int, seed: int) -> list[DocumentView]:
    """Pick documents round-robin across document types.

    Each type gets an equal share until it runs out of documents, and which
    documents are taken within a type is random but repeatable for a seed.

    Args:
        views: Documents to choose from.
        count: How many to pick (fewer if there are not enough).
        seed: Random seed.

    Returns:
        The chosen documents.
    """
    rng = random.Random(seed)  # noqa: S311  (sampling, not security)
    groups: dict[str, list[DocumentView]] = defaultdict(list)
    for view in views:
        groups[view.doc_type].append(view)
    for group in groups.values():
        rng.shuffle(group)
    chosen: list[DocumentView] = []
    while len(chosen) < count and any(groups.values()):
        for doc_type in sorted(groups):
            if groups[doc_type] and len(chosen) < count:
                chosen.append(groups[doc_type].pop())
    return chosen


def build_sheet_rows(
    views: Sequence[DocumentView],
    size: int = DEFAULT_SIZE,
    hard_share: float = DEFAULT_HARD_SHARE,
    seed: int = 42,
) -> list[dict[str, str]]:
    """Choose files to check and format them as sheet rows.

    Args:
        views: All documents.
        size: Total rows wanted.
        hard_share: Fraction of rows taken from the least confident documents.
        seed: Random seed so the same sample is produced every time.

    Returns:
        Sheet rows (fewer than ``size`` if there are not enough documents).
    """
    hard_count = min(round(size * hard_share), len(views))
    by_confidence = sorted(views, key=lambda v: (v.confidence, v.path, v.name))
    hard = by_confidence[:hard_count]
    hard_ids = {v.drive_file_id for v in hard}
    rest = [v for v in views if v.drive_file_id not in hard_ids]
    sample = _balanced_sample(rest, size - hard_count, seed)
    rows: list[dict[str, str]] = []
    for stratum, chosen in ((SPREAD, sample), (HARD, hard)):
        for view in chosen:
            row = dict.fromkeys(SHEET_COLUMNS, "")
            row.update(
                stratum=stratum,
                drive_file_id=view.drive_file_id,
                course=view.course_code,
                path=view.path,
                name=view.name,
                url=view.url,
            )
            for name in FIELDS:
                row[f"pred_{name}"] = _cell(getattr(view, name))
            rows.append(row)
    return rows


def write_sheet(rows: Sequence[dict[str, str]], path: Path) -> None:
    """Write sheet rows to a CSV file.

    Args:
        rows: Rows from :func:`build_sheet_rows`.
        path: Destination (overwritten if present).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows to %s", len(rows), path)


@dataclass(slots=True)
class FieldScore:
    """Right/total counts for one field.

    Attributes:
        right: Rows where the prediction matched the checked value.
        total: Rows that were checked.
    """

    right: int = 0
    total: int = 0

    @property
    def accuracy(self) -> float:
        """Share of checked rows that were right (0 if none were checked)."""
        return self.right / self.total if self.total else 0.0


@dataclass(slots=True)
class StratumScore:
    """Scores for one group of rows (spread or hard).

    Attributes:
        rows: Rows checked in this group.
        rows_correct: Rows marked ``y``.
        fields: Per-field scores.
    """

    rows: int = 0
    rows_correct: int = 0
    fields: dict[str, FieldScore] = field(default_factory=lambda: {n: FieldScore() for n in FIELDS})


@dataclass(slots=True)
class AccuracyReport:
    """Result of scoring a filled-in sheet.

    Attributes:
        unchecked: Rows left blank in the ``correct`` column (ignored).
        strata: Scores per group name.
    """

    unchecked: int = 0
    strata: dict[str, StratumScore] = field(default_factory=dict)


def score_sheet(path: Path) -> AccuracyReport:
    """Score a sheet that a person has filled in.

    Args:
        path: The CSV written by :func:`write_sheet` and then edited.

    Returns:
        Accuracy per group and per field.

    Raises:
        ExtractionError: If the file cannot be read, a ``correct`` cell is not
            ``y``/``n``, or a row marked ``n`` has no ``fix_*`` value.
    """
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError as exc:
        raise ExtractionError(f"Cannot read label sheet {path}: {exc}") from exc

    report = AccuracyReport()
    problems: list[str] = []
    for line, row in enumerate(rows, start=2):
        verdict = (row.get("correct") or "").strip().lower()
        if verdict == "":
            report.unchecked += 1
            continue
        if verdict not in {"y", "n"}:
            problems.append(f"line {line}: 'correct' must be y or n, got {verdict!r}")
            continue
        fixes = {n: (row.get(f"fix_{n}") or "").strip() for n in FIELDS}
        if verdict == "n" and not any(fixes.values()):
            problems.append(f"line {line}: marked n but no fix_* column is filled")
            continue
        score = report.strata.setdefault(row.get("stratum") or SPREAD, StratumScore())
        score.rows += 1
        score.rows_correct += verdict == "y"
        for name in FIELDS:
            prediction = (row.get(f"pred_{name}") or "").strip()
            wrong = verdict == "n" and fixes[name] != "" and fixes[name] != prediction
            score.fields[name].total += 1
            score.fields[name].right += not wrong
    if problems:
        raise ExtractionError("Label sheet problems:\n  " + "\n  ".join(problems[:10]))
    return report
