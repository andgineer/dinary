# Google Sheets Setup

Dinary-server stores runtime data in DuckDB, not in Google Sheets. Google
Sheets are used for two auxiliary flows: bootstrap import of historical data and
optional append-only sheet logging. You need a Google service account and one
or more spreadsheets shared with that account.

## 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/).
2. Click **Select a project** → **New Project** → name it (e.g. `dinary`) → **Create**.
3. Select the newly created project.

## 2. Enable the required APIs

1. Go to **APIs & Services** → **Library**.
2. Search for **Google Sheets API** → click it → **Enable**.
3. Go back to **Library**, search for **Google Drive API** → click it → **Enable**.

## 3. Create a service account

1. Go to **APIs & Services** → **Credentials** → **Create Credentials** → **Service account**.
2. Fill in:
      - **Name**: `dinary` (or any name)
      - **ID**: auto-generated
3. Click **Create and Continue** → skip optional steps → **Done**.

## 4. Download the JSON key

1. In **Credentials**, click the newly created service account.
2. Go to **Keys** → **Add Key** → **Create new key** → **JSON** → **Create**.
3. Save the downloaded file as `~/.config/gspread/service_account.json`:

```bash
mkdir -p ~/.config/gspread
mv ~/Downloads/your-project-*.json ~/.config/gspread/service_account.json
```

!!! warning
    Keep this file secret. Never commit it to Git — it is already in `.gitignore`.

## 5. Create and share the spreadsheet

1. Go to [sheets.google.com](https://sheets.google.com/) → create a new spreadsheet.
2. Name it (e.g. `Dinary Expenses`).
3. Copy the spreadsheet ID from the URL: `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`.
4. Click **Share** → paste the service account email (found in the JSON key as `client_email`, looks like `dinary@project-id.iam.gserviceaccount.com`) → set role to **Editor** → **Send**.

## 6. Configure dinary-server

Runtime Google Sheets settings live in `.deploy/.env` (environment variables):

| Variable | Value |
|----------|-------|
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | path to `service_account.json` (default: `~/.config/gspread/service_account.json`) |
| `DINARY_SHEET_LOGGING_SPREADSHEET` | optional spreadsheet ID or URL for append-only runtime logging (see section 7) |

Bootstrap-import configuration has been moved out of `.env` into a dedicated file — see the next section.

### Bootstrap import sources

The bootstrap import flow (`inv import-config`, `inv import-catalog`, `inv import-budget`, `inv import-budget-all`, `inv import-income`, `inv import-income-all`, `inv verify-bootstrap-import`, `inv verify-bootstrap-import-all`, `inv verify-income-equivalence-all`) reads the list of source spreadsheets from an **optional** file at `.deploy/import_sources.json` in the repo root. The file is gitignored — only the placeholder template at `.deploy.example/import_sources.json` is committed.

If you don't plan to run bootstrap import (e.g. you start from an empty DuckDB and enter expenses only through the PWA), skip this file entirely — the runtime works without it. Seed the runtime taxonomy with `inv bootstrap-catalog` in that case.

To enable bootstrap import, copy the template and edit it:

```bash
cp .deploy.example/import_sources.json .deploy/import_sources.json
$EDITOR .deploy/import_sources.json
```

The schema is a JSON array, one object per year:

```json
[
  {
    "year": 2026,
    "spreadsheet_id": "YOUR_SPREADSHEET_ID_HERE",
    "worksheet_name": "Sheet1",
    "layout_key": "default"
  },
  {
    "year": 2019,
    "spreadsheet_id": "YOUR_SPREADSHEET_ID_HERE",
    "income_worksheet_name": "Balance",
    "income_layout_key": "balance_rub"
  }
]
```

Per-object fields:

- `year` — budget year.
- `spreadsheet_id` — Google Sheets spreadsheet ID.
- `worksheet_name` — worksheet tab for expense import (defaults to empty → first visible tab).
- `layout_key` — sheet layout parser (`default`, `rub_6col`, `rub_2016`, `rub_2014`, `rub_2012`, `rub_fallback`); defaults by year.
- `income_worksheet_name` — optional worksheet tab for income import.
- `income_layout_key` — optional parser for income import (`balance_rub`, `balance_rub_rsd`, `balance_rsd`, `income_rsd`).
- `notes` — optional free-form operator comment.

`.deploy/import_sources.json` is the single source of truth for which years exist: `inv import-*` and `inv verify-*` compute their year list from this file (it used to live in a DuckDB table called `import_sources`; that table was removed in the 2026-04 reset and the config now lives next to the service-account JSON instead of inside derived DB state). Any `inv import-*` command invoked without the file raises a clear error pointing at the repo-root `imports/` directory, which documents the schema and workflows in full.

## 7. Sheet logging (optional)

Sheet logging automatically appends every new expense to a Google Sheets spreadsheet in real time. This is useful if you want to build pivot tables or charts in Google Sheets alongside the built-in Dinary analytics.

### How it works

- Each `POST /api/expenses` appends a row to the **first worksheet** of the configured spreadsheet.
- The 3D category/event/tags are projected to 2D `(sheet_category, sheet_group)` via the `logging_mapping` table. If no mapping exists for a category, the category name is used as a fallback.
- The same worksheet may hold **multiple years** at once. Rows are sorted by `(year, month, sheet_category, sheet_group)`, with newer year/month blocks on top. The worker reads the year from column A's underlying date value (Google displays it as e.g. `Apr-1`, but the cell stores `2026-04-01`), so January 2026 and January 2027 do **not** collide.
- Each appended row carries an opaque idempotency marker `[exp:<expense_id>]` in column J. If a previous append for the same expense reached Google but the response was lost (timeout), the next attempt sees the marker and skips the duplicate write.
- If the append fails (network error, quota), the job stays in a queue and is retried automatically by the in-process periodic drain (every `DINARY_SHEET_LOGGING_DRAIN_INTERVAL_SEC` seconds, default 300; set to `0` to disable).

### Enabling

Set `DINARY_SHEET_LOGGING_SPREADSHEET` to the spreadsheet ID or the full browser URL:

```bash
# Either a bare spreadsheet ID:
DINARY_SHEET_LOGGING_SPREADSHEET=YOUR_SPREADSHEET_ID_HERE

# Or a full URL (the ID is extracted automatically):
DINARY_SHEET_LOGGING_SPREADSHEET=https://docs.google.com/spreadsheets/d/YOUR_SPREADSHEET_ID_HERE/edit
```

The spreadsheet must be shared with the service account from step 4 (Editor role).

### Disabling

Leave `DINARY_SHEET_LOGGING_SPREADSHEET` empty or unset. Expenses are still saved to DuckDB; only the Google Sheets append is skipped.

### Retries are automatic

Pending jobs are retried automatically by the in-process periodic drain task that runs inside the FastAPI process. On startup, all pending jobs are drained immediately; after that, the drain runs every `DINARY_SHEET_LOGGING_DRAIN_INTERVAL_SEC` seconds (default 300). There is no external CLI to trigger — recovery is fully automatic.

**Rate limiting.** Each periodic sweep attempts at most `DINARY_SHEET_LOGGING_DRAIN_MAX_ATTEMPTS_PER_ITERATION` queue rows (default 15) with `DINARY_SHEET_LOGGING_DRAIN_INTER_ROW_DELAY_SEC` seconds pause between attempts (default 1.0). One attempt makes 1-3 Google Sheets API calls (read the idempotency marker, optional append, optional dedupe-cleanup), so steady-state Sheets API usage is between ~3 and ~9 calls/min — comfortably inside the 60/min per-user quota. A backlog of 60 rows recovers in about 20 minutes after a restart; a backlog of 1000 rows takes a few hours. Raise the cap only if you are sure your Sheets quota headroom allows it.

**TTL.** Rows for expenses older than `DINARY_SHEET_LOGGING_DRAIN_MAX_AGE_DAYS` days (default 90) are silently skipped and left in `sheet_logging_jobs`. If you need to log an older expense, delete and re-create it, or manually delete the skipped queue row with DuckDB CLI while the server is stopped.

!!! warning
    There is no external CLI to drain the queue while the server is running. DuckDB enforces single-writer per file across processes, so an external drainer would have to be coordinated with a server stop. The lifespan task is the supported recovery path.
