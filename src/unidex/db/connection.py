"""SQLite connection helpers: opening, schema creation and transactions."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from unidex.exceptions import DatabaseError

logger = logging.getLogger(__name__)

IN_MEMORY = ":memory:"

# Bump when schema.sql changes in a way CREATE IF NOT EXISTS cannot apply.
SCHEMA_VERSION = 2


def connect(db_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection configured the way Unidex expects.

    The connection runs in autocommit mode (``isolation_level=None``) so that
    transactions only ever start where we explicitly call :func:`transaction`.
    Foreign keys are switched on (SQLite leaves them off by default) and rows
    can be read by column name.

    Args:
        db_path: Database file path, or ``":memory:"`` for a throwaway database.

    Returns:
        An open connection. The caller is responsible for closing it.
    """
    if str(db_path) != IN_MEMORY:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an older database up to :data:`SCHEMA_VERSION`.

    Version 1 (Stage 1) created a ``documents`` table with fewer columns and a
    shorter list of allowed values. SQLite cannot change a ``CHECK`` constraint
    in place, and nothing wrote to that table before Stage 3, so the safe
    migration is to drop and recreate it. If it unexpectedly holds rows we
    refuse rather than delete them.

    Args:
        conn: An open connection from :func:`connect`.

    Raises:
        DatabaseError: If the old ``documents`` table contains data.
    """
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version >= SCHEMA_VERSION:
        return
    has_documents = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'documents'"
    ).fetchone()
    if has_documents is not None:
        rows = int(conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        if rows:
            raise DatabaseError(
                f"Cannot migrate: the old documents table holds {rows} rows. "
                "Back up the database and delete it, then re-run the loaders."
            )
        # reports and search_logs point at documents; both are empty at version 1.
        conn.execute("DROP TABLE IF EXISTS reports")
        conn.execute("DROP TABLE IF EXISTS search_logs")
        conn.execute("DROP TABLE documents")
        logger.info("Migrated database from schema version %d to %d", version, SCHEMA_VERSION)


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not exist yet.

    Older databases are migrated first (see :func:`_migrate`).

    Args:
        conn: An open connection from :func:`connect`.
    """
    _migrate(conn)
    schema_sql = resources.files("unidex.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block of statements atomically.

    Either every statement inside the ``with`` block takes effect (commit), or,
    if any exception escapes the block, none of them do (rollback).

    Args:
        conn: An open connection from :func:`connect`.

    Yields:
        The same connection, for convenience.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
