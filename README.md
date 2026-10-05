# Unidex

A web app that unifies scattered campus department drives (PYQs, notes, slides) into one
searchable index, with a chat interface and result cards that link back to the original files.
Search structures (inverted index, BM25, trie) are written from scratch.

**Status:** Stage 6b of 7 (search page, chat, login, reports, live Drive sync). File-content
reading and deployment are next.

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

## Try the search (after loading a CSV)

```powershell
python scripts/search_cli.py "laplace transform guide"
python scripts/search_cli.py --suggest lap
python scripts/benchmark.py
```

## Run the web app (Stage 4)

After loading your CSVs and running `extract_metadata.py`:

```powershell
python scripts/serve.py          # then open http://127.0.0.1:8000
```

The server only listens on your own computer. The interactive API reference is at `/docs`.
The catalog is built once at start-up, so restart the server after loading new data.

## Chat (Stage 5)

The **Chat** tab reads a message such as "OOP midsem papers and notes on inheritance", shows a
form to confirm or edit (course, exam, years, topics, kinds of material), then searches.
Reading messages uses rules only; no language-model request is needed. To measure it:

```powershell
python scripts/eval_chat.py            # scores eval/chat_messages.csv
```

Optional: if `GEMINI_API_KEY` and `GEMINI_MODEL` are set in `.env`, a message whose course the
rules cannot find is sent to Gemini once, which may only pick from the indexed courses.

## Login, reports and logs (Stage 6a)

Locally the site is open (no login). To turn on Google sign-in, create an OAuth client in Google
Cloud Console (type *Web application*, redirect address `http://127.0.0.1:8000/auth/callback`
plus your hosted address later), then put the three values in `.env` (see `.env.example`).
Never paste them into chat or commit them. Only accounts on `ALLOWED_EMAIL_DOMAIN` can sign in.

Every file card has **Wrong info?** and **Broken link?** buttons. Read the reports with:

```powershell
python scripts/list_reports.py
python scripts/list_reports.py --delete-logs-older-than 90
```

## Sync from Google Drive (Stage 6b)

Instead of re-exporting a CSV, Unidex can list the Drive folders itself (read-only; it never
changes or copies your files).

1. Google Cloud Console (same project): enable **Google Drive API**; create an OAuth client of
   type **Desktop app**; put its id and secret in `.env` as `DRIVE_CLIENT_ID` and
   `DRIVE_CLIENT_SECRET`; add the Google account that can open the drives as a test user.
2. `python scripts/drive_login.py` (once, opens your browser; repeat weekly while the OAuth app is
   in Testing mode, because Google expires the sign-in after 7 days).
3. Check before writing anything:
   `python scripts/sync_drive.py --folder <folder id or link> --compare data/real/Data_1_Unidex.csv --dry-run`
   The comparison should say nearly all files are in both with identical folder and name.
   If you picked a course folder itself (say the M3 folder) add `--path-prefix /M3`, so paths read
   like the CSV's (`/M3/Sem 1 .../file.pdf`); the folder name is how the course is recognised.
4. Real run: `python scripts/sync_drive.py --folder <id> --department CS --label "CS archive"`,
   then restart the website. Files deleted from Drive are hidden from search (never deleted
   from the database); a listing that lost over half of a source is refused unless `--force`.

If your college blocks third-party apps from reading its Drive, step 2 fails; keep using the
Apps Script CSV export and `scripts/load_csv.py` instead.

## Extract metadata (Stage 3)

```powershell
python scripts/extract_metadata.py                 # rules only: no LLM calls at all
python scripts/extract_metadata.py --llm gemini    # also ask Gemini about the leftovers
python scripts/review_queue.py export              # writes data/real/review.csv
python scripts/review_queue.py import              # stores your corrections
python scripts/label_sheet.py make                 # ~100 files to check by hand
python scripts/label_sheet.py score                # accuracy after you fill the sheet in
```

The app works fully with `--llm none`. Gemini is only used for the few files the rules cannot
classify, in one batched request, and never more than `LLM_REQUESTS_PER_DAY` per day.
Only folder paths and file names are sent to Gemini, never file contents.

If you already created a database in Stage 1 or 2, running any script upgrades it automatically
(the empty `documents` table is rebuilt). Your `raw_files` are kept.

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
| `src/unidex/search/` | tokenizer, inverted index, BM25, trie, strategies, filtered catalog, stack grouping |
| `src/unidex/api/` | FastAPI app, JSON schemas |
| `src/unidex/auth/` | Google sign-in, the college-domain rule, read-only Drive access |
| `src/unidex/chat/` | message parser, planner, optional Gemini course helper, parser evaluation |
| `web/` | the search page (plain HTML, CSS and JavaScript, no build step) |
| `src/unidex/extraction/` | folder-path parser, rules, Gemini client, daily budget, review queue, labelling |
| `src/unidex/models/` | plain data classes and enums |
| `scripts/` | command-line entry points |
| `eval/` | test queries and benchmark results |
| `data/sample/` | synthetic drive listing (safe to commit) |
| `data/real/` | your real exports (git-ignored, never commit) |
| `docs/decisions.md` | what we chose and why |

## Conventions

Google-style docstrings everywhere, type hints everywhere, `logging` instead of `print`,
custom exceptions instead of bare `except`, no secrets in code (use `.env`).
