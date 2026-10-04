"""Repositories: the only place in Unidex that writes SQL.

Each repository is a small class wrapping one area of the database. The rest of
the code calls methods like ``courses.resolve("OOP")`` and never sees SQL, so
changing a table only means changing this file (the Repository pattern).

Repositories never open transactions themselves. The caller wraps a group of
calls in :func:`unidex.db.connection.transaction`, so related changes succeed
or fail together.
"""

import sqlite3
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date

from unidex.exceptions import IngestionError
from unidex.models.raw_file import RawFile
from unidex.normalize import normalize_alias


class BaseRepository:
    """Holds the database connection shared by all repositories."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Store the connection.

        Args:
            conn: An open connection from :func:`unidex.db.connection.connect`.
        """
        self._conn = conn


class DepartmentRepository(BaseRepository):
    """Reads and writes the ``departments`` table."""

    def get_or_create(self, name: str) -> int:
        """Return the id of a department, creating it if needed.

        Args:
            name: Department name, e.g. ``"CS"``.

        Returns:
            The department's primary key.
        """
        self._conn.execute("INSERT OR IGNORE INTO departments (name) VALUES (?)", (name,))
        row = self._conn.execute("SELECT id FROM departments WHERE name = ?", (name,)).fetchone()
        return int(row["id"])


class SourceRepository(BaseRepository):
    """Reads and writes the ``sources`` table (one row per indexed drive)."""

    def get_or_create(
        self, department_id: int, label: str, drive_folder_id: str | None = None
    ) -> int:
        """Return the id of a source, creating it if needed.

        Args:
            department_id: The department that owns the drive.
            label: Human-readable unique name, e.g. ``"CS archive"``.
            drive_folder_id: Root folder id in Drive, if known.

        Returns:
            The source's primary key.
        """
        self._conn.execute(
            """
            INSERT OR IGNORE INTO sources (department_id, label, drive_folder_id)
            VALUES (?, ?, ?)
            """,
            (department_id, label, drive_folder_id),
        )
        row = self._conn.execute("SELECT id FROM sources WHERE label = ?", (label,)).fetchone()
        return int(row["id"])

    def mark_synced(self, source_id: int, synced_at: str) -> None:
        """Record when a source was last synchronised.

        Args:
            source_id: The source's primary key.
            synced_at: ISO-8601 UTC timestamp.
        """
        self._conn.execute(
            "UPDATE sources SET last_synced_at = ? WHERE id = ?", (synced_at, source_id)
        )


class CourseRepository(BaseRepository):
    """Reads and writes ``courses`` and ``course_aliases``."""

    def upsert(self, code: str, name: str, department_id: int) -> int:
        """Insert a course, or update its name and department if the code exists.

        Args:
            code: Course code, e.g. ``"CS F213"``.
            name: Full course name.
            department_id: Owning department.

        Returns:
            The course's primary key.
        """
        self._conn.execute(
            """
            INSERT INTO courses (code, name, department_id) VALUES (?, ?, ?)
            ON CONFLICT (code) DO UPDATE SET
                name = excluded.name,
                department_id = excluded.department_id
            """,
            (code, name, department_id),
        )
        row = self._conn.execute("SELECT id FROM courses WHERE code = ?", (code,)).fetchone()
        return int(row["id"])

    def add_alias(self, alias: str, course_id: int, verified: bool = False) -> None:
        """Register a nickname for a course.

        Re-adding an alias for the same course just refreshes it.

        Args:
            alias: Nickname as written, e.g. ``"OOP"``.
            course_id: The course it refers to.
            verified: Whether a human has confirmed this mapping.

        Raises:
            IngestionError: If the alias is empty after normalisation, or it
                already points at a different course.
        """
        key = normalize_alias(alias)
        if not key:
            raise IngestionError(f"Alias {alias!r} has no letters or digits")
        existing = self._conn.execute(
            "SELECT course_id FROM course_aliases WHERE alias_norm = ?", (key,)
        ).fetchone()
        if existing is not None and int(existing["course_id"]) != course_id:
            raise IngestionError(f"Alias {alias!r} already maps to a different course")
        self._conn.execute(
            """
            INSERT INTO course_aliases (alias_norm, alias, course_id, verified)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (alias_norm) DO UPDATE SET
                alias = excluded.alias,
                verified = excluded.verified
            """,
            (key, alias, course_id, int(verified)),
        )

    def resolve(self, text: str) -> int | None:
        """Find the course a nickname, code or name refers to.

        Args:
            text: Anything a student might type, e.g. ``"oop"`` or ``"CS F213"``.

        Returns:
            The course id, or ``None`` if nothing matches.
        """
        row = self._conn.execute(
            "SELECT course_id FROM course_aliases WHERE alias_norm = ?", (normalize_alias(text),)
        ).fetchone()
        return None if row is None else int(row["course_id"])


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    """Outcome of a bulk upsert.

    Attributes:
        inserted: Rows that did not exist before.
        updated: Rows that already existed and were refreshed.
    """

    inserted: int
    updated: int


class RawFileRepository(BaseRepository):
    """Reads and writes the ``raw_files`` table."""

    _UPSERT_SQL = """
        INSERT INTO raw_files (
            source_id, drive_file_id, path, name, extension, mime_type,
            modified_on, url, is_indexable, first_seen_at, last_seen_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (drive_file_id) DO UPDATE SET
            source_id = excluded.source_id,
            path = excluded.path,
            name = excluded.name,
            extension = excluded.extension,
            mime_type = excluded.mime_type,
            modified_on = excluded.modified_on,
            url = excluded.url,
            is_indexable = excluded.is_indexable,
            last_seen_at = excluded.last_seen_at
    """

    def upsert_many(self, source_id: int, files: Iterable[RawFile], seen_at: str) -> UpsertCounts:
        """Insert new files and refresh ones already known (matched by Drive id).

        Args:
            source_id: The source the files were listed from.
            files: The files to store.
            seen_at: ISO-8601 UTC timestamp of this listing.

        Returns:
            How many rows were inserted versus updated.
        """
        params = [
            (
                source_id,
                f.drive_file_id,
                f.path,
                f.name,
                f.extension,
                f.mime_type,
                None if f.modified_on is None else f.modified_on.isoformat(),
                f.url,
                int(f.is_indexable),
                seen_at,
                seen_at,
            )
            for f in files
        ]
        before = self.count()
        self._conn.executemany(self._UPSERT_SQL, params)
        inserted = self.count() - before
        return UpsertCounts(inserted=inserted, updated=len(params) - inserted)

    def count(self, indexable_only: bool = False) -> int:
        """Count stored files.

        Args:
            indexable_only: If true, count only files flagged as study material.

        Returns:
            The number of matching rows.
        """
        sql = "SELECT COUNT(*) AS n FROM raw_files"
        if indexable_only:
            sql += " WHERE is_indexable = 1"
        return int(self._conn.execute(sql).fetchone()["n"])

    def iter_all(self) -> Iterator[RawFile]:
        """Yield every stored file, ordered by path then name.

        Yields:
            One :class:`RawFile` per row.
        """
        cursor = self._conn.execute(
            """
            SELECT drive_file_id, path, name, extension, mime_type, modified_on, url, is_indexable
            FROM raw_files ORDER BY path, name
            """
        )
        for row in cursor:
            yield RawFile(
                drive_file_id=row["drive_file_id"],
                path=row["path"],
                name=row["name"],
                extension=row["extension"],
                mime_type=row["mime_type"],
                modified_on=None
                if row["modified_on"] is None
                else date.fromisoformat(row["modified_on"]),
                url=row["url"],
                is_indexable=bool(row["is_indexable"]),
            )
