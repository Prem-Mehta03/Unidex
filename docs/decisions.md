# Decisions log

Short record of what was chosen and why. Newest at the bottom.

## Product

- **Name:** Unidex (unified + index). `.ai` domain optional later.
- **First drive:** CS department archive; Mathematics and Computing next.
- **Interface:** chat-first with a plain search mode; results are grouped "stack" cards that open
  the original file in Drive. No downloads or file copies.
- **Exams:** quiz, midsem and compre are all supported. Quizzes are numbered (Quiz 1, Quiz 2) and
  usually cover only part of the syllabus.
- **Login:** Google OAuth restricted to the college domain, in Testing mode (100 test users).
- **LLM:** Gemini free tier. Limits observed: 5 requests/minute, 20 requests/day, shared by all
  users. Design consequence: rules first, LLM only for what rules cannot decide, in batches.

## Data findings (from the first real export, 500 rows)

- The export was capped at 500 rows, so it covers only `DD` and `M3` under `2-1 CDCs`. `OOP`,
  `LCS` and `DISCO` are missing; re-run the Apps Script per course folder.
- Folder pattern: `/<group>/<course nickname>/<semester folder>/<category>/...`.
- Semester folders come in two shapes: `Sem 1 25-26 (Instructor)` and `25-26 Sem 1 (Instructor)`.
  The extractor must handle both.
- Category folders carry meaning: `Midsem`, `Compre`, `Quizzes`, `Evals`, `Slides`, `Tutorials`,
  `Labs`, `Cheatsheets`, `Topicwise Guides`. Slide sub-folders carry lecturer names and sometimes
  "Pre Midsem" / "Post Midsem".
- Course folders use nicknames (`DD`, `M3`), so an alias table maps them to course codes.
  `data/course_aliases.csv` is a **draft**: every row has `verified=false` until checked.
  `DD` may be cross-listed under several codes (CS/EEE/INSTR F215).
- 235 of 500 files are study material (PDF, slides, scans, Google Docs). The rest are Verilog lab
  sources (`.v`), simulator output, archives and extension-less build files. They are stored as
  raw files but flagged `is_indexable = 0`.
- Dates in the export are month/day/year.

## Engineering

- SQLite for now; all SQL lives in `db/repositories.py`.
- Two-layer data model: `raw_files` (exactly what the drive listed) and `documents` (interpreted
  metadata). Raw data is never lost if the extractor improves.
- Drive file id is the identity of a file, not its path, so moves and renames update one row.
- `FileSource` interface: CSV today, Drive API in Stage 6, same sync job.
