-- Unidex database schema (SQLite).
--
-- Layers:
--   departments / sources / courses / course_aliases : reference data
--   raw_files   : every file exactly as listed by the drive (ingestion output)
--   documents   : interpreted metadata per searchable file (extractor output)
--   search_logs / reports : usage data and user feedback
--
-- Every statement is idempotent (IF NOT EXISTS) so init_schema() can be re-run.
-- Foreign keys are enforced per connection with PRAGMA foreign_keys = ON.

CREATE TABLE IF NOT EXISTS departments (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS courses (
    id            INTEGER PRIMARY KEY,
    code          TEXT NOT NULL UNIQUE,            -- e.g. 'CS F213'
    name          TEXT NOT NULL,                   -- e.g. 'Object Oriented Programming'
    department_id INTEGER NOT NULL REFERENCES departments (id)
);

-- Nicknames used in folder names and by students ('OOP', 'DD', 'M3', ...).
-- alias_norm is the normalised lookup key (see unidex.normalize.normalize_alias).
CREATE TABLE IF NOT EXISTS course_aliases (
    alias_norm TEXT PRIMARY KEY,
    alias      TEXT NOT NULL,
    course_id  INTEGER NOT NULL REFERENCES courses (id) ON DELETE CASCADE,
    verified   INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1))
);

-- One row per drive we index (e.g. the CS department archive).
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    department_id   INTEGER NOT NULL REFERENCES departments (id),
    label           TEXT NOT NULL UNIQUE,
    drive_folder_id TEXT,                          -- filled in when the Drive API is used
    last_synced_at  TEXT                           -- ISO-8601 UTC timestamp
);

-- Raw ingestion output. drive_file_id is the identity: re-syncing the same
-- file updates the row instead of creating a duplicate.
CREATE TABLE IF NOT EXISTS raw_files (
    id            INTEGER PRIMARY KEY,
    source_id     INTEGER NOT NULL REFERENCES sources (id),
    drive_file_id TEXT NOT NULL UNIQUE,
    path          TEXT NOT NULL,
    name          TEXT NOT NULL,
    extension     TEXT NOT NULL DEFAULT '',
    mime_type     TEXT NOT NULL,
    modified_on   TEXT,                            -- ISO date 'YYYY-MM-DD'
    url           TEXT NOT NULL,
    is_indexable  INTEGER NOT NULL CHECK (is_indexable IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

-- Interpreted metadata. Filled in by the extractor (Stage 3).
-- exam_type / doc_type / link_status values mirror unidex.models.enums.
CREATE TABLE IF NOT EXISTS documents (
    id                    INTEGER PRIMARY KEY,
    raw_file_id           INTEGER NOT NULL UNIQUE REFERENCES raw_files (id) ON DELETE CASCADE,
    course_id             INTEGER REFERENCES courses (id),
    title                 TEXT NOT NULL,
    drive_url             TEXT NOT NULL,
    mime_type             TEXT NOT NULL,
    academic_year         INTEGER,                 -- start year, 2025 for '25-26'
    semester              INTEGER,
    instructor            TEXT,
    exam_type             TEXT NOT NULL DEFAULT 'unknown'
        CHECK (exam_type IN ('quiz', 'midsem', 'compre', 'lab_compre', 'none', 'unknown')),
    exam_number           INTEGER,                 -- Quiz 1, Quiz 2, ...
    doc_type              TEXT NOT NULL DEFAULT 'unknown'
        CHECK (doc_type IN ('pyq', 'solution', 'notes', 'slides', 'tutorial', 'lab',
                            'cheatsheet', 'other', 'unknown')),
    content_hash          TEXT,
    link_status           TEXT NOT NULL DEFAULT 'unknown'
        CHECK (link_status IN ('unknown', 'ok', 'broken', 'no_access')),
    extraction_method     TEXT CHECK (extraction_method IN ('rule', 'llm', 'manual')),
    extraction_confidence REAL CHECK (extraction_confidence BETWEEN 0 AND 1),
    reviewed              INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS search_logs (
    id                  INTEGER PRIMARY KEY,
    user_hash           TEXT NOT NULL,
    query               TEXT NOT NULL,
    filters             TEXT,                      -- JSON text
    result_count        INTEGER NOT NULL,
    clicked_document_id INTEGER REFERENCES documents (id),
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    type        TEXT NOT NULL,                     -- 'wrong_info' | 'broken_link'
    note        TEXT,
    created_at  TEXT NOT NULL
);

-- Indexes. Stage 4 benchmarks queries with and without these using EXPLAIN.
CREATE INDEX IF NOT EXISTS idx_raw_files_source ON raw_files (source_id);
CREATE INDEX IF NOT EXISTS idx_raw_files_indexable ON raw_files (is_indexable);
CREATE INDEX IF NOT EXISTS idx_documents_course_year_exam
    ON documents (course_id, academic_year, exam_type);
CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents (content_hash);
