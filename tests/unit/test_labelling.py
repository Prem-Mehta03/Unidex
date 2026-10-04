import csv
from pathlib import Path

import pytest

from unidex.db.repositories import DocumentView
from unidex.exceptions import ExtractionError
from unidex.extraction.labelling import (
    FIELDS,
    HARD,
    SHEET_COLUMNS,
    SPREAD,
    build_sheet_rows,
    score_sheet,
    write_sheet,
)


def view(index: int, confidence: float = 0.9) -> DocumentView:
    return DocumentView(
        f"id{index}", f"/OOP/p{index}", f"f{index}.pdf", "u", "pyq", "quiz", 1, 2025, False, False,
        confidence, "rule", "CS F213",
    )  # fmt: skip


def test_sheet_mixes_a_spread_sample_and_hardest_rows() -> None:
    views = [view(i, confidence=0.9) for i in range(50)] + [
        view(100 + i, confidence=0.2) for i in range(5)
    ]
    rows = build_sheet_rows(views, size=20, hard_share=0.25)
    assert len(rows) == 20
    hard = [r for r in rows if r["stratum"] == HARD]
    assert len(hard) == 5
    assert all(r["drive_file_id"].startswith("id1") for r in hard)  # the five low-confidence ones
    assert len({r["drive_file_id"] for r in rows}) == 20  # no file twice


def test_same_seed_gives_the_same_sample() -> None:
    views = [view(i) for i in range(80)]
    assert build_sheet_rows(views, size=30, seed=1) == build_sheet_rows(views, size=30, seed=1)
    assert build_sheet_rows(views, size=30, seed=1) != build_sheet_rows(views, size=30, seed=2)


def test_small_corpus_returns_what_exists() -> None:
    assert len(build_sheet_rows([view(i) for i in range(3)], size=100)) == 3


def fill(path: Path, edits: dict[int, dict[str, str]]) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for index, change in edits.items():
        rows[index].update(change)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_scoring_counts_per_field_and_ignores_blank_rows(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(build_sheet_rows([view(i) for i in range(10)], size=10, hard_share=0), sheet)
    fill(
        sheet,
        {
            0: {"correct": "y"},
            1: {"correct": "y"},
            2: {"correct": "n", "fix_exam_type": "midsem"},
            3: {"correct": "n", "fix_academic_year": "2024", "fix_exam_number": "1"},
        },
    )
    report = score_sheet(sheet)
    assert report.unchecked == 6
    score = report.strata[SPREAD]
    assert (score.rows, score.rows_correct) == (4, 2)
    assert (score.fields["exam_type"].right, score.fields["exam_type"].total) == (3, 4)
    assert (score.fields["academic_year"].right, score.fields["academic_year"].total) == (3, 4)
    assert score.fields["exam_number"].right == 4  # fix equals the prediction (1)
    assert score.fields["doc_type"].accuracy == 1.0


def test_n_without_any_fix_is_rejected(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(build_sheet_rows([view(0)], size=1, hard_share=0), sheet)
    fill(sheet, {0: {"correct": "n"}})
    with pytest.raises(ExtractionError, match="no fix_"):
        score_sheet(sheet)


def test_correct_column_must_be_y_or_n(tmp_path: Path) -> None:
    sheet = tmp_path / "sheet.csv"
    write_sheet(build_sheet_rows([view(0)], size=1, hard_share=0), sheet)
    fill(sheet, {0: {"correct": "maybe"}})
    with pytest.raises(ExtractionError, match="must be y or n"):
        score_sheet(sheet)


def test_fields_list_matches_the_sheet_columns() -> None:
    for name in FIELDS:
        assert f"pred_{name}" in SHEET_COLUMNS
        assert f"fix_{name}" in SHEET_COLUMNS


def typed_view(index: int, doc_type: str) -> DocumentView:
    base = view(index)
    return DocumentView(
        base.drive_file_id, base.path, base.name, base.url, doc_type, base.exam_type,
        base.exam_number, base.academic_year, base.is_makeup, base.has_solution,
        base.confidence, base.method, base.course_code,
    )  # fmt: skip


def test_spread_sample_does_not_let_one_type_crowd_out_the_others() -> None:
    views = [typed_view(i, "slides") for i in range(100)]
    views += [typed_view(200 + i, "handout") for i in range(5)]
    views += [typed_view(300 + i, "cheatsheet") for i in range(5)]
    rows = build_sheet_rows(views, size=20, hard_share=0)
    kinds = [r["pred_doc_type"] for r in rows]
    assert kinds.count("handout") == 5
    assert kinds.count("cheatsheet") == 5
    assert kinds.count("slides") == 10
