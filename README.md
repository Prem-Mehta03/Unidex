# Unidex

A web app that unifies scattered campus department drives (PYQs, notes, slides) into one
searchable index, with a chat interface and result cards that link back to the original files.
Search structures (inverted index, BM25, trie) are written from scratch.

**Status:** Stage 1 of 7 (skeleton, database, ingestion). Search, LLM extractor, API and UI come
in later stages.

## Setup (Windows, PowerShell)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env
python scripts/init_db.py
python scripts/load_csv.py --csv data/sample/sample_drive.csv
```

On macOS/Linux, activate with `source .venv/bin/activate` and copy with `cp`.

Run everything from the repository root.

## Checks

```powershell
pytest              # tests
ruff check .        # lint (also forbids print())
ruff format .       # formatting
mypy                # type checking
```

## Layout

| Folder | Job |
|---|---|
| `src/unidex/db/` | schema and all SQL (repositories) |
| `src/unidex/ingestion/` | read a drive listing, store raw files |
| `src/unidex/models/` | plain data classes and enums |
| `scripts/` | command-line entry points |
| `data/sample/` | synthetic drive listing (safe to commit) |
| `data/real/` | your real exports (git-ignored, never commit) |
| `docs/decisions.md` | what we chose and why |

## Conventions

Google-style docstrings everywhere, type hints everywhere, `logging` instead of `print`,
custom exceptions instead of bare `except`, no secrets in code (use `.env`).
