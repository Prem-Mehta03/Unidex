import importlib.util
import sqlite3
from pathlib import Path

from unidex.db.repositories import list_file_kinds
from unidex.db.seed import seed_courses
from unidex.extraction.pipeline import ExtractionPipeline
from unidex.ingestion.csv_source import CsvFileSource
from unidex.ingestion.sync_job import SyncJob

from ..conftest import ALIASES_CSV, REPO_ROOT, SAMPLE_CSV, fixed_clock


def load_script():
    spec = importlib.util.spec_from_file_location(
        "probe_pdfs", Path(REPO_ROOT) / "scripts" / "probe_pdfs.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def loaded(conn: sqlite3.Connection) -> sqlite3.Connection:
    seed_courses(conn, ALIASES_CSV)
    SyncJob(conn, CsvFileSource(SAMPLE_CSV), "CS", "CS archive", clock=fixed_clock).run()
    ExtractionPipeline(conn, clock=fixed_clock).run()
    return conn


def test_file_kinds_cover_every_visible_document(conn: sqlite3.Connection) -> None:
    rows = list_file_kinds(loaded(conn))
    assert len(rows) == conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert {"pdf", "pptx"} <= {r.extension for r in rows}


def test_hidden_documents_are_not_listed(conn: sqlite3.Connection) -> None:
    loaded(conn)
    conn.execute(
        "UPDATE documents SET link_status = 'broken' WHERE id = (SELECT MIN(id) FROM documents)"
    )
    assert (
        len(list_file_kinds(conn))
        == conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] - 1
    )


def test_sample_is_only_pdfs_capped_per_type_and_repeatable(conn: sqlite3.Connection) -> None:
    script = load_script()
    rows = list_file_kinds(loaded(conn))
    first = script.pick_sample(rows, ["pyq", "slides"], 2, seed=5)
    again = script.pick_sample(rows, ["pyq", "slides"], 2, seed=5)
    other = script.pick_sample(rows, ["pyq", "slides"], 2, seed=6)
    assert first == again
    assert all(r.extension == "pdf" for r in first)
    assert {r.doc_type for r in first} <= {"pyq", "slides"}
    assert sum(r.doc_type == "pyq" for r in first) <= 2
    assert len(script.pick_sample(rows, ["pyq"], 999, seed=1)) == sum(
        r.doc_type == "pyq" and r.extension == "pdf" for r in rows
    )
    assert isinstance(other, list)
