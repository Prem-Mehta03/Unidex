# Deploying Unidex on Render (free)

## What lives where

| What | Where | Survives a restart? |
|---|---|---|
| Code | Public repository (`main`) and the private one (`deploy`) | yes |
| Catalog (files, labels, paper text) | `deploy/catalog.db` on the `deploy` branch of the private repository | yes (it is part of the deploy) |
| Reports and search logs | A Google Sheet you own (copy sent by the site) | **yes** |
| The same reports and logs in the server's own SQLite file | Render's temporary disk | **no** (wiped on every restart, sleep and deploy) |
| Secrets (Google client secret, session secret) | Render's environment settings | yes |
| Login sessions | Signed cookie in the student's browser | yes |

Render's free web service has no permanent disk, sleeps after 15 minutes without visits
(about one minute to wake) and has 750 free hours a month, which is enough for one service
awake all month. So nothing important is kept only on the server.

## 1. Prepare the database copy (your computer)

```powershell
python scripts/export_deploy_db.py
```

This writes `deploy/catalog.db`: your catalog without any reports or search logs.

## 2. Two GitHub repositories: public code, private catalog

The code, invented sample data and screenshots are fine to show the world, but
`deploy/catalog.db` contains your drives' file names, links and paper text and must **never**
be public. So there are two repositories and one extra branch:

* **`unidex` (public)**: branch `main`, code only. `deploy/catalog.db` is git-ignored here, so
  `git add .` cannot put it on `main` by accident.
* **`unidex-deploy` (private)**: only the branch `deploy`, which is `main` plus the catalog
  file. Render builds from this one.

On github.com create both empty repositories (the second one **private**). Then in the project
folder:

```powershell
git init
git add .
git status
```

Read the list from `git status`. It must **not** contain `.env`, anything with `token` in its
name, `data/real/` or any `.db` file. If it looks right:

```powershell
git commit -m "Unidex"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/unidex.git
git push -u origin main

git remote add private https://github.com/YOUR-NAME/unidex-deploy.git
git checkout -b deploy
git config branch.deploy.remote private
git config branch.deploy.pushRemote private
python scripts/export_deploy_db.py
git add -f deploy/catalog.db
git commit -m "Catalog snapshot"
git push -u private deploy
git checkout main
```

Switching back to `main` removes `deploy/catalog.db` from your folder (it only exists on the `deploy` branch); that is expected, and `export_deploy_db.py` recreates it.

The two `git config` lines make `git push` from the `deploy` branch go to the private
repository only. Never push `deploy` to `origin`.

## 3. Create the Sheet that keeps reports and logs

1. Create a new Google Sheet (any name, for example "Unidex events").
2. Extensions > Apps Script. Delete the sample code, paste all of `deploy/apps_script_sink.gs`.
3. Project Settings (gear) > Script properties > Add: name `SECRET`, value a long random
   string (30+ characters). Keep it; the website needs the same value.
4. Deploy > New deployment > type **Web app** > Execute as **Me** > Who has access **Anyone**
   > Deploy. Approve the permission screen (it is your own script writing to your own Sheet).
5. Copy the **Web app URL** (ends in `/exec`).

"Anyone" only means anyone who knows the URL *and* the secret can add rows; without the secret
the script answers "forbidden" and writes nothing.

## 4. Create the Render service

1. render.com > sign up with GitHub > New > **Blueprint** > pick the private repository
   `unidex-deploy` and the branch **`deploy`**. Render reads `render.yaml`.
2. Fill in the values it asks for:
   * `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`: the same *Web application* client you use
     on your computer.
   * `ALLOWED_EMAILS`: your own address(es), comma separated (optional; college addresses are
     allowed by domain already).
   * `EVENT_WEBHOOK_URL`: the Web app URL from step 3.
   * `EVENT_WEBHOOK_SECRET`: the `SECRET` from step 3.
   `SESSION_SECRET` is generated for you.
3. Create. The first build takes a few minutes. Render shows the site address, for example
   `https://unidex-ab12.onrender.com`. The site finds its own address (`RENDER_EXTERNAL_URL`),
   so you do not need to set `UNIDEX_PUBLIC_URL`.

## 5. Tell Google about the new address

Google Cloud console > APIs & Services > Credentials > your Web client > Authorised redirect
URIs > add `https://YOUR-SITE.onrender.com/auth/callback` > Save (it can take a few minutes).

You do **not** need to add students as test users: the site asks Google only for the basic
profile (sign-in) permissions, which Google lets any account use even while the project is in
Testing mode. The college-domain rule is enforced by Unidex itself.

## 6. Check it

* `https://YOUR-SITE.onrender.com/api/health` shows `{"status":"ok","documents":...}`.
* Sign in with a college account: works. Sign in with a personal Gmail: refused with a message.
* Run a search, then press "Wrong info?" on a result. Within about 10 seconds the Sheet should
  show a `search` tab and a `report` tab.
* `https://YOUR-SITE.onrender.com/docs` should say Not Found (the API pages are hidden).

## 7. Keep it awake (optional)

Without visits the site sleeps and the first student waits about a minute. A free monitor such
as UptimeRobot can request `/api/health` every 5 minutes. One always-awake service uses about
744 of the 750 free hours, so this only works if it is your only free service on Render.

## 8. Updating the data

```powershell
python scripts/drive_login.py        # if your Drive sign-in is older than 7 days
python scripts/sync_drive.py ...     # as before
python scripts/extract_metadata.py
python scripts/read_contents.py

git checkout deploy
git merge main                       # bring the latest code across
python scripts/export_deploy_db.py
git add -f deploy/catalog.db
git commit -m "Update catalog"
git push
git checkout main
```

Render redeploys by itself after the push. Code changes go to `main` and `origin` as usual; they
reach the website the next time you merge `main` into `deploy` and push.

## Things to know

* **Reports.** `scripts/list_reports.py` only sees reports in the database on your computer.
  On the hosted site, read reports in the Sheet. After fixing a label, run the extractor
  locally as usual and ship the new catalog.
* **Search logs** are useful: download the `search` tab as CSV to find real student wording
  for `eval/chat_messages.csv`. Users are stored as a one-way hash, never as email addresses.
  Tell students that queries are logged.
* **Gemini is off** on the hosted site unless you also set `GEMINI_API_KEY` and `GEMINI_MODEL`.
  Its daily request counter lives in the temporary database and would reset on every restart,
  so the free-tier daily limit could be exceeded; leave it off until that is handled.
* **Memory.** With every paper's text loaded the site needs roughly 100 to 150 MB, under the
  512 MB of the free plan.
* **Prices and limits change.** Check Render's free-plan page if something behaves differently.
