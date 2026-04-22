# Google Sheets Setup

Dinary server stores runtime data in DuckDB, not in Google Sheets. In the
public/admin deployment story, Google Sheets are used only for optional
append-only sheet logging. You need a Google service account and a spreadsheet
shared with that account.

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

## 6. Configure dinary

Runtime Google Sheets settings live in `.deploy/.env` (environment variables):

| Variable | Value |
|----------|-------|
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | path to `service_account.json` (default: `~/.config/gspread/service_account.json`) |
| `DINARY_SHEET_LOGGING_SPREADSHEET` | optional spreadsheet ID or URL for append-only runtime logging (see below) |

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
