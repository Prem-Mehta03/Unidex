"""Make a clean copy of the database to ship to a web host.

The copy keeps everything the website reads (courses, files, documents, extracted text) and
leaves out everything people produced while using it (reports, search logs, model-usage
counts), so private activity is never uploaded with the code.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from unidex.db.connection import connect
from unidex.exceptions import UnidexError

# Tables that hold activity rather than catalog data.
ACTIVITY_TABLES = ("reports", "search_logs", "llm_usage")
GITHUB_WARN_BYTES = 50 * 1024 * 1024


class ExportError(UnidexError):
    """The snapshot could not be made."""


@dataclass(frozen=True, slots=True)
class SnapshotInfo:
    """What a snapshot contains.

    Attributes:
        path: The new file.
        size_bytes: Its size.
        documents: Visible documents in it.
        texts: Documents with stored text.
    """

    path: Path
    size_bytes: int
    documents: int
    texts: int


def export_snapshot(source: Path, target: Path) -> SnapshotInfo:
    """Copy ``source`` to ``target`` without activity tables' rows.

    Args:
        source: The working database.
        target: Where to write the clean copy (replaced if it exists).

    Returns:
        A short description of the copy.

    Raises:
        ExportError: If the source is missing, empty, or is the same file as the target.
    """
    if not source.is_file() or source.stat().st_size == 0:
        raise ExportError(f"Database not found or empty: {source}")
    if source.resolve() == target.resolve():
        raise ExportError("The snapshot must be a different file from the database")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    src = connect(source)
    try:
        src.execute("VACUUM INTO ?", (str(target),))
    except sqlite3.Error as exc:
        raise ExportError(f"Could not copy the database: {exc}") from exc
    finally:
        src.close()
    copy = connect(target)
    try:
        for table in ACTIVITY_TABLES:
            copy.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed names, not user input
        copy.execute("VACUUM")
        documents = copy.execute(
            "SELECT COUNT(*) FROM documents WHERE link_status != 'broken'"
        ).fetchone()[0]
        texts = copy.execute(
            "SELECT COUNT(*) FROM document_text WHERE method != 'none'"
        ).fetchone()[0]
    finally:
        copy.close()
    return SnapshotInfo(target, target.stat().st_size, int(documents), int(texts))
