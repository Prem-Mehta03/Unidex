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

## Search (Stage 2)

- The searchable text of a file is its name plus its folder path, because folder names carry
  the course nickname, semester, instructor and category (Midsem, Slides, ...).
- Tokenizer: lower-case, split on punctuation, split long words glued to numbers
  (`Lecture06` becomes `lecture 6`), strip leading zeros, drop a few stopwords, strip a plural
  `s`. Short letter prefixes stay joined because they are course codes (`m3`, `f213`).
  Documents and queries go through the same function.
- Search strategies share one interface: `NaiveSearch` (scan every document, count matching
  words, no weighting) and `Bm25Search` (inverted index, BM25, trie autocomplete). Autocomplete
  uses unstemmed words so suggestions are readable.
- BM25 parameters: `k1 = 1.5`, `b = 0.75`, idf `ln(1 + (N - n + 0.5) / (n + 0.5))` (never
  negative). Repeated query words count once. Ties go to the lower document id so results are
  deterministic.
- `Bm25Scorer.search` accepts `allowed_ids`: the hook for metadata filters (course, year, exam
  type) in Stage 4, so scoring only touches documents that pass the filters.
- Document ids in the search layer are positions in the corpus list; they switch to
  `documents.id` when the API arrives (Stage 4).

## Search findings (first real export, 235 searchable files, 28 starter queries)

Numbers below come from one run in a sandbox; re-run `python scripts/benchmark.py` on your own
machine before quoting any figure.

- **Quality: no measurable difference.** hit@5 is 28/28 for both strategies. hit@1 is 24/28 for
  the naive scan and 25/28 for BM25. MRR@10 is 0.929 against 0.932. The corpus is small and most
  queries are answerable from file and folder words, so the two cannot be told apart yet. Do not
  claim that BM25 improves relevance until a harder query set (real student queries from the
  logs) shows it. Most of the gain over Drive filename search comes from the shared tokenizer,
  not from BM25 weighting.
- **Speed: depends on the query.** At 23,500 documents (every real document repeated 100 times)
  the mean time per query was 5.3 ms for the scan and 4.6 ms for the index. For queries made of
  rare words the index is far faster (`eigenvalues` 0.10 ms against 2.06 ms; `laplace transform
  guide` 0.38 ms against 2.81 ms). For queries made of very common words it is slower
  (`m3 quiz 2 key 25-26` 17.3 ms against 10.0 ms), because a common word's postings list covers
  most of the corpus and each posting costs more in Python than one set intersection in the scan.
  Index build time is about twice the scan's (882 ms against 413 ms).
- The scaled corpus is artificial: repeating every file keeps the vocabulary fixed, so common
  words stay common as it grows. A real drive that grows by adding courses gains new rare words.
- A first optimisation (constants hoisted out of the scoring loop, lengths read directly) removed
  the index's overall speed deficit. Further options: skip or demote very common words, process
  rare words first, prune with score upper bounds (WAND / MaxScore). The larger fix is Stage 4:
  filtering by course, year and exam type *before* scoring so common words stop mattering.
- Known weaknesses that structured metadata (Stage 3) and query parsing (Stage 5) should fix:
  the year `2024` does not match the folder `24-25`; `question paper` is not linked to `QP`;
  `digital design` is not linked to `DD`; path words from another course's folder can outrank
  the right file (`m3 compre solution` surfaces DD lab-compre files).

## Metadata extraction (Stage 3)

Data: all four exports loaded (1,576 files, 869 searchable after skipping lab web-page images).
`DD` and `M3` are still the 500-row export, so they are incomplete; `OOP`, `LCS` and `DISCO` are
under the cap and complete.

- **Rules first, model last.** Rules classify 857 of 869 searchable files without any model call;
  the other 12 fit in one Gemini request. The app must keep working with zero LLM calls, so
  `--llm none` is the default.
- **Each decision carries a confidence.** Explicit keyword in the name 0.95; folder name 0.85;
  a guess 0.6 or less. A file's confidence is its weakest decision. Below 0.7 (or any unknown
  field that matters) the file goes to the review queue.
- **Year is the hardest field.** A folder such as `Past Material` sits inside one year's folder
  but holds papers from older years, so the folder year is ignored there; the year comes from the
  file name (`Quiz 1 2018`, `LiCS1617...`) or stays unknown. A lone year in a name (`2018`) is
  weak evidence and a span (`15-16`) is strong. `Lecture-16-17` is a lecture range, not a year.
- **New vocabulary** found in the data: exam type `test` (old "Test 1" papers: not assumed to be
  quizzes), `lab_quiz` (OOP lab quizzes, LCS lab tests); document types `assignment`, `handout`,
  `textbook`, `grade_stats` (the "Av Details" docs and the LCS "Insights" marks distributions);
  flags `is_makeup`, `has_solution`; `syllabus_scope` (`pre_midsem` / `post_midsem`, read from
  slide folders such as `Danumjaya (Pre Midsem)`). Scope is what "I have a midsem" will filter on.
- **Query-time implication.** `test` and `quiz` should be treated as related when the chat
  parser (Stage 5) turns "quiz" into a filter.
- **Language model:** Gemini over plain HTTPS, key in a request header, never logged. Many files
  per request. The model may only fill fields the rules left unknown; it never overwrites a rule.
  Its output is validated strictly (JSON array, allowed values only, ids it was given); bad items
  are dropped one by one. File names are treated as untrusted text. Its answers are marked
  `llm`, get confidence 0.65 and always appear in the review queue. Answers are stored, so a
  re-run does not spend requests again.
- **Daily budget** is stored in the database (so a restart cannot reset it) and counted per
  Pacific-time day, which is when Google resets free-tier quotas. A failed request still counts.
  On any provider error the model phase stops and the remaining files stay in the review queue.
- **Human corrections win.** `review_queue import` marks rows `manual` and `reviewed`; later
  automatic runs skip them. The import validates the whole file first and applies it in one
  transaction.
- **Student ids.** One file in the LCS 21-22 compre folder is named after a student id
  (`2020A7PS0114G`), probably a student's answer script. It is flagged in the review queue;
  decide whether it should be shown at all.
- **Schema version 2.** `documents` gained columns and wider CHECK lists; because SQLite cannot
  alter a CHECK, the empty old table is dropped and recreated (refusing if it has rows).
- **Not done yet:** duplicate detection (the same file sits in two folders: `Evals` and
  `Evals/Lab Tests`), instructor-name normalisation (`Neena` vs `Neena Goveas`, `RPJ` vs
  `Ramprasad S. Joshi`), reading file contents.

### Honest limits

- Gemini integration was written to the documented REST shape and tested against a fake
  transport only. It has not been run against the real service from here (no key, and the
  sandbox cannot reach it). The first real run is the real test: expect to adjust the model id or
  response handling.
- No accuracy figure is claimed yet. The label sheet exists to produce one; see below.


## Web API and search page (Stage 4)

- **Catalog in memory.** At start-up the server reads every document once and builds the BM25
  index, the autocomplete trie and a filter index (for each facet value, the set of document ids).
  Requests never touch the database, so they are fast and there is no SQLite threading to worry
  about. The price: restart the server after loading new data.
- **Filters before ranking.** Different facets combine with AND (set intersection, smallest set
  first), values inside one facet with OR (union). BM25 receives the allowed ids, so it never
  scores excluded documents. Ranking inside "course = OOP" fixes the problem that BM25 treats
  "oop" as just another word.
- **Facet counts** for a facet ignore that facet's own filter, so a student can still see and add
  the other courses after choosing one.
- **Course nicknames in the query** ("oop", "dd", "m3", "cs f213", "logic in cs") become a
  course filter and are removed from the text. Shown as a chip that can be removed ("search the
  words as typed instead"). If the student already picked a course in the sidebar, that choice
  wins; a nickname for the same course is just dropped from the text. Nicknames come from the
  `course_aliases` table, which is still unverified (`verified = false`), so check
  `data/course_aliases.csv`.
- **Stacks.** Hits are grouped by course and kind. A paper and its solution share a stack per exam
  type ("Object Oriented Programming · Midsem papers"); every other document type is its own kind.
  Stacks are ordered by their best BM25 score; papers inside a stack read newest first, other
  material best match first. Files whose exam is unknown share one "Exam papers" stack.
- **Honest labels.** A card shows "Checked" when a human reviewed the tags and "Tags unsure" when
  nobody did and confidence is below 0.7.
- **Safety.** The page inserts text with `textContent` only; links are shown only if they start
  with `http(s)://` (checked on the server and in the browser); links open with
  `rel="noopener noreferrer"`. A Content-Security-Policy allows only our own scripts and Google
  Fonts. There is no login yet: do not expose the server beyond your own computer until Stage 6.
- **Not in Stage 4:** the chat tab, "Wrong info?" / "Broken link?" reports (they need the reports
  table and login), search logging, duplicate collapsing, a relative-score cut-off for weak BM25
  matches. A three-word query can still match hundreds of files through its weakest word; stacks
  put the best first and "Show more stacks" pages the rest.


## Chat (Stage 5)

- **Rules first, form second.** A message is read by rules (course via the catalog's alias scan,
  years, exam, kind of material, quiz number; leftover words become topics). The result is a
  small form the student confirms or edits; only then is anything searched. The model never
  writes replies, links or file names.
- **Stateless server.** The browser sends the last form back with each message. A message
  changes only the fields it mentions ("show only 2022 solutions"); "also / include / add"
  adds instead of replacing; "start over" forgets the old form. A fresh request without a
  course therefore inherits the previous course; say "start over" to drop it.
- **Papers and notes are searched differently.** Papers and solutions are found by exam type and
  year (their names rarely mention topics), so topics are NOT applied to them and the card says
  so. Notes, slides, tutorials are found by topic through BM25 on file and folder names; if no
  name mentions the topics, all of that material is listed and the student is told.
- **"Last N years"** counts the newest N years that have papers of the asked exam, not calendar
  years. A lone "2023" is read as academic year 2023-24 and a note says so.
- **Midsem-only target** hides slides marked post-midsem. Quiz and test are treated as related.
- **Optional Gemini help** only picks a course from the indexed list when the rules found none.
  Answers are validated, each use counts against the shared daily request budget, and the chat
  never waits for a rate limit. The course helper has been tested only with a fake client.
- **Measured, with caveats.** `eval/chat_messages.csv` has 41 messages I wrote, including
  deliberately hard ones. The rules were tuned after seeing the first run, so the score is not a
  fair held-out figure. Replace or extend it with real student messages before quoting it.
- **Known gaps:** course wording not in the alias list ("discrete maths"), "latest" on its own,
  unknown-course words end up as a topic, topic search inside paper contents (needs reading
  files), a topic list split on "and" ("stochastic calculus and finance" becomes two topics).


## Chat fixes (Stage 5.1)

- **"Latest" means newest year with papers.** "latest", "newest", "most recent" and "recent"
  (without a number) become "last 1 year", which is counted over the years that actually have
  papers for that course and exam, so a missing current year never empties the answer.
- **Unknown subjects say so.** When no course is found but the message has topic words, the chat
  says the course may not be in the drives yet and lists the courses it does have.
- **Empty results** say the material may not have been added yet.

## Login, reports and logs (Stage 6a)

- **Domain check has two parts.** The email must end in `@<allowed domain>` and Google must send
  the `hd` (hosted domain) claim naming the same domain. Without the second check anybody could
  make a personal Google account that uses a college address.
- **ID token is trusted without a signature check** because it comes straight from Google's token
  address over HTTPS (allowed by the OpenID Connect rules). Issuer, audience, expiry, nonce and
  `email_verified` are still checked. PKCE and a random `state` protect the redirect.
- **Cookie sessions** (signed, HttpOnly, SameSite=Lax, Secure when the public address is https,
  7 days). No server-side session table.
- **Open locally, closed when configured.** Without Google settings the site is open. Setting
  `UNIDEX_REQUIRE_LOGIN=true` makes the server refuse to start unless login is fully set up.
- **Privacy.** Logs and reports hold a keyed hash of the email (HMAC, 16 hex characters), never
  the address. Search text is stored, so trim it with `list_reports.py --delete-logs-older-than`.
  The Search tab logs queries of 3+ characters and skips an identical repeat within 10 seconds;
  typing pauses can still log a few partial queries, so Stage 7 should merge prefix chains.
  Chat logs only when "Search drives" is pressed, not for the live estimate.
- **Reports.** Same file and same type within 24 hours is stored once. Ten reports per person
  per hour. Reports never change the index by themselves; a person reads them.
- **Hosting caveat.** Free hosts such as Render wipe local files on every deploy, which would
  delete reports and logs stored in SQLite. Decide before deploying (see Stage 6 deploy notes).


## Drive sync (Stage 6b)

- **Direct REST calls, no Google client library.** The Drive `files.list` endpoint is one GET;
  `httpx2` plus a small retry loop (429/5xx and rate-limit 403s, 1-2-4-8 second pauses) is easier
  to test with a fake transport than the large official library.
- **Read-only scope** (`drive.readonly`), refresh token in a git-ignored file with owner-only
  permissions. In Testing mode Google expires it after 7 days; the error message says so.
- **A Desktop-app OAuth client for syncing, separate from the website's Web client.** Loopback
  redirects need no registered addresses, and the website never holds Drive access.
- **Same shape as the CSV export.** Paths are folder names below the chosen root, names kept
  verbatim (the export has folders like "Midsem " with a trailing space). `--compare` checks a
  fresh listing against the old CSV by Drive id, folder path and name before anything is written.
- **A failed listing writes nothing.** The whole tree is read before the transaction opens; any
  folder that still fails after retries aborts the run.
- **Vanished files are hidden, not deleted.** A complete Drive listing marks documents whose
  file was not seen as `broken` (left out of search) and restores them if they return, so
  reports and reviews keep their target. A listing under half of a source of 20+ files is
  refused (probably lost permission or a partial read) unless `--force`.
- **Shortcuts** to folders are followed once each (loops end); shortcuts to files are listed
  as the target file.
- **Not tested against real Drive** from the build sandbox; all behaviour is covered with a faked
  Drive API, so run `--dry-run --compare` first.


## Reading file contents (Stage 6d)

- **Hybrid reading.** A 60-PDF probe found 34 text, 12 mixed, 13 scanned, 1 error; papers are
  the weak spot. So: text layer first (pypdf), OCR only for pages without text (at most 12 pages
  per file, 200 DPI), and a quality gate for the rest.
- **Quality gate.** The share of word-like tokens must be at least 0.65 and at least half the
  pages need text. Handwriting yields garbled text (0.3 to 0.6) and is stored as `none` rather
  than polluting search.
- **Stored in SQLite** (`document_text`), capped at 60k characters, 40 pages per file. One row
  per file, saved immediately, so runs resume; a file is re-read when its Drive modified time
  changes. Download failures are not stored and are retried.
- **Papers.** Matched by content first, then the solutions that belong to a matched paper, then
  unreadable papers labelled unchecked. Readable papers that do not mention the topic are left out.
- **Known limits.** Handwritten notes and solutions stay unsearchable; maths symbols extract
  poorly; `.pptx` is not read; name-based and content-based BM25 scores are mixed on different
  scales; the free-text Search tab does not use content yet. The text deploys with the database.
