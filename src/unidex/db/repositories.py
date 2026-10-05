"""Repositories: the only place in Unidex that writes SQL.

Each repository is a small class wrapping one area of the database. The rest of
the code calls methods like ``courses.resolve("OOP")`` and never sees SQL, so
changing a table only means changing this file (the Repository pattern).

Repositories never open transactions themselves. The caller wraps a group of
calls in :func:`unidex.db.connection.transaction`, so related changes succeed
or fail together.
"""

import sqlite3
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date

from unidex.exceptions import IngestionError
from unidex.models.metadata import ExtractedMetadata
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

    def count_for_source(self, source_id: int) -> int:
        """Count the files stored for one source.

        Args:
            source_id: The source's primary key.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM raw_files WHERE source_id = ?", (source_id,)
        ).fetchone()
        return int(row["n"])

    def retire_unseen(self, source_id: int, seen_at: str) -> tuple[int, int]:
        """Hide documents whose file vanished from a source and restore ones that came back.

        A file counts as vanished when the listing at ``seen_at`` did not include it, so its
        ``last_seen_at`` is older than ``seen_at``. Vanished documents get
        ``link_status = 'broken'`` and are left out of search; documents that are listed
        again get ``'ok'``. Rows are never deleted, so reports and reviews keep their target.

        Args:
            source_id: The source that was just listed in full.
            seen_at: ISO-8601 UTC timestamp of that listing.

        Returns:
            ``(newly hidden, restored)``.
        """
        hidden = self._conn.execute(
            """
            UPDATE documents SET link_status = 'broken'
            WHERE link_status != 'broken' AND raw_file_id IN (
                SELECT id FROM raw_files WHERE source_id = ? AND last_seen_at < ?)
            """,
            (source_id, seen_at),
        ).rowcount
        restored = self._conn.execute(
            """
            UPDATE documents SET link_status = 'ok'
            WHERE link_status = 'broken' AND raw_file_id IN (
                SELECT id FROM raw_files WHERE source_id = ? AND last_seen_at >= ?)
            """,
            (source_id, seen_at),
        ).rowcount
        return hidden, restored

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

    def iter_indexable(self) -> Iterator["StoredFile"]:
        """Yield every searchable file with its database id, ordered by path then name.

        Yields:
            One :class:`StoredFile` per searchable row.
        """
        cursor = self._conn.execute(
            """
            SELECT id, drive_file_id, path, name, extension, mime_type, modified_on, url,
                   is_indexable
            FROM raw_files WHERE is_indexable = 1 ORDER BY path, name
            """
        )
        for row in cursor:
            yield StoredFile(
                raw_file_id=int(row["id"]),
                file=RawFile(
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
                ),
            )


@dataclass(frozen=True, slots=True)
class StoredFile:
    """A raw file together with its database id.

    Attributes:
        raw_file_id: Primary key in ``raw_files``.
        file: The file record.
    """

    raw_file_id: int
    file: RawFile


class DocumentRepository(BaseRepository):
    """Reads and writes the ``documents`` table (interpreted metadata)."""

    # Rows a human has reviewed are never overwritten by a later automatic run.
    _UPSERT_SQL = """
        INSERT INTO documents (
            raw_file_id, course_id, title, drive_url, mime_type, academic_year, semester,
            instructor, exam_type, exam_number, doc_type, is_makeup, has_solution,
            syllabus_scope, extraction_method, extraction_confidence, extraction_notes,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (raw_file_id) DO UPDATE SET
            course_id = excluded.course_id,
            title = excluded.title,
            drive_url = excluded.drive_url,
            mime_type = excluded.mime_type,
            academic_year = excluded.academic_year,
            semester = excluded.semester,
            instructor = excluded.instructor,
            exam_type = excluded.exam_type,
            exam_number = excluded.exam_number,
            doc_type = excluded.doc_type,
            is_makeup = excluded.is_makeup,
            has_solution = excluded.has_solution,
            syllabus_scope = excluded.syllabus_scope,
            extraction_method = excluded.extraction_method,
            extraction_confidence = excluded.extraction_confidence,
            extraction_notes = excluded.extraction_notes
        WHERE documents.reviewed = 0
    """

    def upsert_many(self, items: Iterable[tuple[StoredFile, ExtractedMetadata]], now: str) -> None:
        """Store extraction results, keeping rows that a human already reviewed.

        Args:
            items: Pairs of the stored file and its extracted metadata.
            now: ISO-8601 UTC timestamp used for ``created_at`` of new rows.
        """
        params = [
            (
                stored.raw_file_id,
                meta.course_id,
                meta.title,
                stored.file.url,
                stored.file.mime_type,
                meta.academic_year,
                meta.semester,
                meta.instructor,
                meta.exam_type.value,
                meta.exam_number,
                meta.doc_type.value,
                int(meta.is_makeup),
                int(meta.has_solution),
                None if meta.syllabus_scope is None else meta.syllabus_scope.value,
                meta.method.value,
                meta.confidence,
                "; ".join(meta.notes),
                now,
            )
            for stored, meta in items
        ]
        self._conn.executemany(self._UPSERT_SQL, params)

    def ids_by_raw_file(self) -> dict[int, tuple[int, bool]]:
        """Map each raw file id to ``(document id, reviewed)``."""
        rows = self._conn.execute("SELECT raw_file_id, id, reviewed FROM documents")
        return {int(r["raw_file_id"]): (int(r["id"]), bool(r["reviewed"])) for r in rows}

    def llm_answers(self) -> dict[int, tuple[str, str, int | None, bool]]:
        """Return earlier language-model answers so a re-run does not ask again.

        Returns:
            Mapping from raw file id to ``(doc_type, exam_type, exam_number,
            is_makeup)`` for rows currently labelled ``llm`` and not reviewed.
        """
        rows = self._conn.execute(
            """
            SELECT raw_file_id, doc_type, exam_type, exam_number, is_makeup
            FROM documents WHERE extraction_method = 'llm' AND reviewed = 0
            """
        )
        return {
            int(r["raw_file_id"]): (
                r["doc_type"],
                r["exam_type"],
                r["exam_number"],
                bool(r["is_makeup"]),
            )
            for r in rows
        }

    def count(self) -> int:
        """Count stored documents."""
        return int(self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])

    def count_by(self, column: str) -> dict[str, int]:
        """Count documents grouped by one of a fixed set of columns.

        Args:
            column: ``doc_type``, ``exam_type`` or ``extraction_method``.

        Returns:
            Mapping from value to number of documents, largest first.

        Raises:
            ValueError: If ``column`` is not one of the allowed names. (The
                name is placed in the SQL text, so it is checked against a
                whitelist instead of being trusted.)
        """
        if column not in {"doc_type", "exam_type", "extraction_method"}:
            raise ValueError(f"Cannot group by {column!r}")
        rows = self._conn.execute(
            f"SELECT {column} AS value, COUNT(*) AS n FROM documents "  # noqa: S608
            "GROUP BY 1 ORDER BY 2 DESC"
        ).fetchall()
        return {str(row["value"]): int(row["n"]) for row in rows}

    def mark_manual(
        self,
        drive_file_id: str,
        fields: dict[str, str | int | None],
    ) -> bool:
        """Apply a human correction and lock the row against automatic overwrites.

        Args:
            drive_file_id: Drive id of the file whose document is corrected.
            fields: Column names and new values. Only the columns in
                :data:`EDITABLE_COLUMNS` are accepted.

        Returns:
            True if a document was updated, False if none matches the id.

        Raises:
            ValueError: If a column is not editable.
        """
        unknown = set(fields) - EDITABLE_COLUMNS
        if unknown:
            raise ValueError(f"Columns are not editable: {sorted(unknown)}")
        assignments = ", ".join(f"{column} = ?" for column in fields)
        sql = (
            f"UPDATE documents SET {assignments}"  # noqa: S608
            + (", " if assignments else "")
            + "extraction_method = 'manual', extraction_confidence = 1.0, reviewed = 1 "
            "WHERE raw_file_id = (SELECT id FROM raw_files WHERE drive_file_id = ?)"
        )
        cursor = self._conn.execute(sql, (*fields.values(), drive_file_id))
        return cursor.rowcount > 0


# Columns a reviewer may change. Everything else is derived and stays untouched.
EDITABLE_COLUMNS = frozenset(
    {
        "doc_type",
        "exam_type",
        "exam_number",
        "academic_year",
        "is_makeup",
        "has_solution",
        "syllabus_scope",
    }
)


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """One file waiting for a human check.

    Attributes:
        drive_file_id: Drive id of the file.
        reason: Why it needs a look.
        path: Folder path.
        name: File name.
        url: Link to open the file.
        doc_type: Current document type.
        exam_type: Current exam type.
        exam_number: Current quiz/test number, if any.
        academic_year: Current academic year, if known.
        is_makeup: Current make-up flag.
        has_solution: Current has-solution flag.
        syllabus_scope: Current scope, or empty string.
    """

    drive_file_id: str
    reason: str
    path: str
    name: str
    url: str
    doc_type: str
    exam_type: str
    exam_number: int | None
    academic_year: int | None
    is_makeup: bool
    has_solution: bool
    syllabus_scope: str


class ReviewQueueRepository(BaseRepository):
    """Reads and writes the ``review_queue`` table."""

    def sync(self, entries: Mapping[int, str], now: str) -> tuple[int, int]:
        """Make the open queue match the documents that currently need review.

        Documents in ``entries`` get an open row (reason refreshed). Open rows
        for documents no longer in ``entries`` are marked resolved, because
        better rules or a language-model answer fixed them.

        Args:
            entries: Mapping from document id to the reason it needs review.
            now: ISO-8601 UTC timestamp.

        Returns:
            ``(opened, resolved)`` counts of rows opened or auto-resolved.
        """
        previously_open = {
            int(row["document_id"])
            for row in self._conn.execute(
                "SELECT document_id FROM review_queue WHERE status = 'open'"
            )
        }
        self._conn.executemany(
            """
            INSERT INTO review_queue (document_id, reason, status, created_at)
            VALUES (?, ?, 'open', ?)
            ON CONFLICT (document_id) DO UPDATE SET
                reason = excluded.reason, status = 'open', resolved_at = NULL
            """,
            [(doc_id, reason, now) for doc_id, reason in entries.items()],
        )
        stale = sorted(previously_open - set(entries))
        self._conn.executemany(
            "UPDATE review_queue SET status = 'resolved', resolved_at = ? WHERE document_id = ?",
            [(now, doc_id) for doc_id in stale],
        )
        return len(set(entries) - previously_open), len(stale)

    def resolve_for_drive_ids(self, drive_file_ids: Iterable[str], now: str) -> None:
        """Mark the queue rows of the given files as resolved.

        Args:
            drive_file_ids: Drive ids of files a human has just reviewed.
            now: ISO-8601 UTC timestamp.
        """
        self._conn.executemany(
            """
            UPDATE review_queue SET status = 'resolved', resolved_at = ?
            WHERE document_id IN (
                SELECT d.id FROM documents d JOIN raw_files r ON r.id = d.raw_file_id
                WHERE r.drive_file_id = ?
            )
            """,
            [(now, drive_id) for drive_id in drive_file_ids],
        )

    def count_open(self) -> int:
        """Count files still waiting for review."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM review_queue WHERE status = 'open'")
        return int(row.fetchone()["n"])

    def iter_open(self) -> Iterator[ReviewItem]:
        """Yield open review items, the least certain first.

        Yields:
            One :class:`ReviewItem` per open row.
        """
        cursor = self._conn.execute(
            """
            SELECT r.drive_file_id, q.reason, r.path, r.name, r.url, d.doc_type, d.exam_type,
                   d.exam_number, d.academic_year, d.is_makeup, d.has_solution,
                   COALESCE(d.syllabus_scope, '') AS syllabus_scope
            FROM review_queue q
            JOIN documents d ON d.id = q.document_id
            JOIN raw_files r ON r.id = d.raw_file_id
            WHERE q.status = 'open'
            ORDER BY d.extraction_confidence, r.path, r.name
            """
        )
        for row in cursor:
            yield ReviewItem(
                drive_file_id=row["drive_file_id"],
                reason=row["reason"],
                path=row["path"],
                name=row["name"],
                url=row["url"],
                doc_type=row["doc_type"],
                exam_type=row["exam_type"],
                exam_number=row["exam_number"],
                academic_year=row["academic_year"],
                is_makeup=bool(row["is_makeup"]),
                has_solution=bool(row["has_solution"]),
                syllabus_scope=row["syllabus_scope"],
            )


class LlmUsageRepository(BaseRepository):
    """Counts language-model requests per day (``llm_usage`` table)."""

    def requests_on(self, day: str) -> int:
        """Return how many requests were made on a day.

        Args:
            day: ISO date ``YYYY-MM-DD`` in the provider's quota time zone.
        """
        row = self._conn.execute("SELECT requests FROM llm_usage WHERE day = ?", (day,)).fetchone()
        return 0 if row is None else int(row["requests"])

    def record_request(self, day: str) -> int:
        """Add one request to a day's count and return the new total.

        Args:
            day: ISO date ``YYYY-MM-DD`` in the provider's quota time zone.
        """
        self._conn.execute(
            """
            INSERT INTO llm_usage (day, requests) VALUES (?, 1)
            ON CONFLICT (day) DO UPDATE SET requests = requests + 1
            """,
            (day,),
        )
        return self.requests_on(day)


@dataclass(frozen=True, slots=True)
class DocumentView:
    """A document joined with the raw file it came from.

    Attributes:
        drive_file_id: Drive id of the file.
        path: Folder path.
        name: File name.
        url: Link to open the file.
        doc_type: Document type.
        exam_type: Exam type.
        exam_number: Quiz or test number, if any.
        academic_year: Start year of the academic year, if known.
        is_makeup: Make-up flag.
        has_solution: Whether solutions are included.
        confidence: Extraction confidence, 0 to 1.
        method: ``rule``, ``llm`` or ``manual``.
        course_code: Course code such as ``CS F213``, or empty.
        course_name: Course name such as ``Object Oriented Programming``, or empty.
        semester: Semester number (1 or 2), or ``None`` when unknown.
        instructor: Instructor name from the folder, or empty.
        syllabus_scope: ``pre_midsem``, ``post_midsem`` or empty (slides only).
    """

    drive_file_id: str
    path: str
    name: str
    url: str
    doc_type: str
    exam_type: str
    exam_number: int | None
    academic_year: int | None
    is_makeup: bool
    has_solution: bool
    confidence: float
    method: str
    course_code: str
    course_name: str = ""
    semester: int | None = None
    instructor: str = ""
    syllabus_scope: str = ""


@dataclass(frozen=True, slots=True)
class FileKindRow:
    """One document's file type, for planning content extraction.

    Attributes:
        drive_file_id: Drive id of the file.
        name: File name.
        extension: Lower-case extension without the dot.
        doc_type: Document type value, e.g. ``pyq``.
        course_code: Course code, or empty.
    """

    drive_file_id: str
    name: str
    extension: str
    doc_type: str
    course_code: str


def list_file_kinds(conn: sqlite3.Connection) -> list[FileKindRow]:
    """List every visible document with its file extension and type.

    Args:
        conn: An open connection.

    Returns:
        One row per document that has not been hidden as gone from Drive.
    """
    rows = conn.execute(
        """
        SELECT r.drive_file_id, r.name, r.extension, d.doc_type, COALESCE(c.code, '') AS code
        FROM documents d
        JOIN raw_files r ON r.id = d.raw_file_id
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE d.link_status != 'broken'
        ORDER BY r.path, r.name
        """
    ).fetchall()
    return [
        FileKindRow(
            drive_file_id=r["drive_file_id"],
            name=r["name"],
            extension=r["extension"],
            doc_type=r["doc_type"],
            course_code=r["code"],
        )
        for r in rows
    ]


def iter_document_views(conn: sqlite3.Connection) -> Iterator[DocumentView]:
    """Yield every document joined with its raw file and course code.

    Args:
        conn: An open connection.

    Yields:
        One :class:`DocumentView` per document whose file still exists in Drive
        (documents marked ``broken`` by a sync are left out), ordered by path then name.
    """
    cursor = conn.execute(
        """
        SELECT r.drive_file_id, r.path, r.name, r.url, d.doc_type, d.exam_type, d.exam_number,
               d.academic_year, d.is_makeup, d.has_solution, d.extraction_confidence,
               d.extraction_method, COALESCE(c.code, '') AS course_code,
               COALESCE(c.name, '') AS course_name, d.semester AS semester,
               COALESCE(d.instructor, '') AS instructor,
               COALESCE(d.syllabus_scope, '') AS syllabus_scope
        FROM documents d
        JOIN raw_files r ON r.id = d.raw_file_id
        LEFT JOIN courses c ON c.id = d.course_id
        WHERE d.link_status != 'broken'
        ORDER BY r.path, r.name
        """
    )
    for row in cursor:
        yield DocumentView(
            drive_file_id=row["drive_file_id"],
            path=row["path"],
            name=row["name"],
            url=row["url"],
            doc_type=row["doc_type"],
            exam_type=row["exam_type"],
            exam_number=row["exam_number"],
            academic_year=row["academic_year"],
            is_makeup=bool(row["is_makeup"]),
            has_solution=bool(row["has_solution"]),
            confidence=float(row["extraction_confidence"] or 0.0),
            method=row["extraction_method"] or "",
            course_code=row["course_code"],
            course_name=row["course_name"],
            semester=row["semester"],
            instructor=row["instructor"],
            syllabus_scope=row["syllabus_scope"],
        )


def load_alias_map(conn: sqlite3.Connection) -> dict[str, str]:
    """Return every course nickname as ``{normalised alias: course code}``.

    Args:
        conn: An open connection.

    Returns:
        For example ``{"oop": "CS F213", "dd": "CS F215"}``.
    """
    rows = conn.execute(
        """
        SELECT a.alias_norm, c.code
        FROM course_aliases a
        JOIN courses c ON c.id = a.course_id
        """
    )
    return {row["alias_norm"]: row["code"] for row in rows}


@dataclass(frozen=True, slots=True)
class ReportRow:
    """One student report about a file.

    Attributes:
        id: Report number.
        drive_file_id: Drive id of the reported file.
        name: File name.
        url: Link to the file.
        type: ``wrong_info`` or ``broken_link``.
        note: What the student wrote (may be empty).
        created_at: UTC time the report was first made.
    """

    id: int
    drive_file_id: str
    name: str
    url: str
    type: str
    note: str
    created_at: str


class ReportRepository(BaseRepository):
    """Student reports about files (``reports`` table)."""

    def add(
        self,
        drive_file_id: str,
        report_type: str,
        note: str,
        now: str,
        *,
        duplicate_since: str,
    ) -> tuple[int, bool] | None:
        """Record a report, unless the same problem was already reported recently.

        Args:
            drive_file_id: Drive id of the file the student reported.
            report_type: ``wrong_info`` or ``broken_link``.
            note: Optional explanation (may be empty).
            now: UTC time as ISO text.
            duplicate_since: A report of the same type on the same file made at or after
                this time counts as a duplicate and is not stored again.

        Returns:
            ``(report id, is_new)``, or ``None`` if no indexed file has that Drive id.
        """
        row = self._conn.execute(
            """
            SELECT d.id FROM documents d JOIN raw_files r ON r.id = d.raw_file_id
            WHERE r.drive_file_id = ?
            """,
            (drive_file_id,),
        ).fetchone()
        if row is None:
            return None
        document_id = int(row["id"])
        existing = self._conn.execute(
            "SELECT id FROM reports WHERE document_id = ? AND type = ? AND created_at >= ?",
            (document_id, report_type, duplicate_since),
        ).fetchone()
        if existing is not None:
            return int(existing["id"]), False
        cursor = self._conn.execute(
            "INSERT INTO reports (document_id, type, note, created_at) VALUES (?, ?, ?, ?)",
            (document_id, report_type, note or None, now),
        )
        return int(cursor.lastrowid or 0), True

    def recent(self, limit: int = 50) -> list[ReportRow]:
        """Return the newest reports first.

        Args:
            limit: Maximum number of reports to return.
        """
        rows = self._conn.execute(
            """
            SELECT p.id, r.drive_file_id, r.name, r.url, p.type,
                   COALESCE(p.note, '') AS note, p.created_at
            FROM reports p
            JOIN documents d ON d.id = p.document_id
            JOIN raw_files r ON r.id = d.raw_file_id
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            ReportRow(
                id=int(r["id"]),
                drive_file_id=r["drive_file_id"],
                name=r["name"],
                url=r["url"],
                type=r["type"],
                note=r["note"],
                created_at=r["created_at"],
            )
            for r in rows
        ]


class SearchLogRepository(BaseRepository):
    """What people searched for (``search_logs`` table). Holds no email addresses."""

    def add(
        self, user_hash: str, query: str, filters_json: str, result_count: int, now: str
    ) -> int:
        """Store one search.

        Args:
            user_hash: Non-reversible label for the person (see ``auth.policy.user_hash``).
            query: The text searched for.
            filters_json: Filters and source as JSON text.
            result_count: How many files matched.
            now: UTC time as ISO text.

        Returns:
            The new row id.
        """
        cursor = self._conn.execute(
            """
            INSERT INTO search_logs (user_hash, query, filters, result_count, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_hash, query, filters_json, result_count, now),
        )
        return int(cursor.lastrowid or 0)

    def count(self) -> int:
        """Return how many searches are stored."""
        return int(self._conn.execute("SELECT COUNT(*) FROM search_logs").fetchone()[0])

    def delete_before(self, cutoff: str) -> int:
        """Delete searches older than a time.

        Args:
            cutoff: UTC time as ISO text; rows with ``created_at`` before it are removed.

        Returns:
            How many rows were deleted.
        """
        cursor = self._conn.execute("DELETE FROM search_logs WHERE created_at < ?", (cutoff,))
        return cursor.rowcount


@dataclass(frozen=True, slots=True)
class ReadTask:
    """A document whose contents still need to be read.

    Attributes:
        document_id: Primary key in ``documents``.
        drive_file_id: Drive id of the file.
        name: File name.
        modified_on: The file's modified date (ISO text), or ``None``.
    """

    document_id: int
    drive_file_id: str
    name: str
    modified_on: str | None


class DocumentTextRepository(BaseRepository):
    """Text read from inside files (``document_text`` table)."""

    def pending(self, doc_types: Collection[str], *, force: bool = False) -> list[ReadTask]:
        """List PDF documents that have no stored text yet, or whose file changed since.

        Args:
            doc_types: Only documents of these types.
            force: Also include documents that already have text.

        Returns:
            Tasks ordered by path then name.
        """
        if not doc_types:
            return []
        marks = ",".join("?" for _ in doc_types)
        stale = (
            "" if force else "AND (t.document_id IS NULL OR t.source_modified IS NOT r.modified_on)"
        )
        rows = self._conn.execute(
            f"""
            SELECT d.id, r.drive_file_id, r.name, r.modified_on
            FROM documents d
            JOIN raw_files r ON r.id = d.raw_file_id
            LEFT JOIN document_text t ON t.document_id = d.id
            WHERE r.extension = 'pdf' AND d.link_status != 'broken'
              AND d.doc_type IN ({marks}) {stale}
            ORDER BY r.path, r.name
            """,  # noqa: S608 - only "?" placeholders and fixed text are formatted in
            tuple(doc_types),
        ).fetchall()
        return [
            ReadTask(int(r["id"]), r["drive_file_id"], r["name"], r["modified_on"]) for r in rows
        ]

    def save(
        self,
        document_id: int,
        method: str,
        quality: float,
        pages: int,
        text: str,
        source_modified: str | None,
        now: str,
    ) -> None:
        """Store (or replace) the text read from one document.

        Args:
            document_id: Primary key in ``documents``.
            method: ``pdf_text``, ``ocr`` or ``none``.
            quality: 0 to 1 (see ``content.quality``).
            pages: Pages in the file.
            text: The text (empty when ``method`` is ``none``).
            source_modified: The file's modified date when it was read.
            now: UTC time as ISO text.
        """
        self._conn.execute(
            """
            INSERT INTO document_text
                (document_id, method, quality, pages, text, source_modified, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (document_id) DO UPDATE SET
                method = excluded.method, quality = excluded.quality, pages = excluded.pages,
                text = excluded.text, source_modified = excluded.source_modified,
                created_at = excluded.created_at
            """,
            (document_id, method, quality, pages, text, source_modified, now),
        )

    def counts_by_method(self) -> dict[str, int]:
        """Return how many documents were read with each method."""
        rows = self._conn.execute(
            "SELECT method, COUNT(*) AS n FROM document_text GROUP BY method"
        ).fetchall()
        return {r["method"]: int(r["n"]) for r in rows}


def load_searchable_texts(conn: sqlite3.Connection, min_quality: float) -> dict[str, str]:
    """Load the text of every visible document that was read well enough to search.

    Args:
        conn: An open connection.
        min_quality: Texts below this quality (0 to 1) are left out.

    Returns:
        ``{drive file id: text}``.
    """
    rows = conn.execute(
        """
        SELECT r.drive_file_id, t.text
        FROM document_text t
        JOIN documents d ON d.id = t.document_id
        JOIN raw_files r ON r.id = d.raw_file_id
        WHERE t.method != 'none' AND t.quality >= ? AND d.link_status != 'broken'
        """,
        (min_quality,),
    ).fetchall()
    return {r["drive_file_id"]: r["text"] for r in rows}
