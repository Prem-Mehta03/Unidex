"""SQLite connection helpers: opening, schema creation and transactions."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

IN_MEMORY = ":memory:"


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


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes if they do not exist yet.

    Args:
        conn: An open connection from :func:`connect`.
    """
    schema_sql = resources.files("unidex.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)


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
