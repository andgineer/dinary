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

Set these environment variables (in `.env` or your hosting's env config):

| Variable | Value |
|----------|-------|
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | path to `service_account.json` (default: `~/.config/gspread/service_account.json`) |
| `DINARY_IMPORT_SOURCES_JSON` | JSON array describing the yearly source spreadsheets used by bootstrap import |

### Bootstrap import sources

`DINARY_IMPORT_SOURCES_JSON` is used by the historical import flow (`inv import-config`, `inv import-catalog`, `inv import-budget`, `inv import-budget-all`, `inv verify-bootstrap-import`, `inv verify-bootstrap-import-all`).

Example:

```bash
DINARY_IMPORT_SOURCES_JSON=[{"year":2026,"spreadsheet_id":"1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms","worksheet_name":"Sheet1","layout_key":"default"}]
```

Each row describes one source spreadsheet for one year. Common fields:

- `year` — budget year
- `spreadsheet_id` — Google Sheets spreadsheet ID
- `worksheet_name` — worksheet tab name for expense import
- `layout_key` — sheet layout parser (`default`, `rub_6col`, etc.)
- `income_worksheet_name` — optional worksheet tab for income import
- `income_layout_key` — optional parser for income import

## 7. Sheet logging (optional)

Sheet logging automatically appends every new expense to a Google Sheets spreadsheet in real time. This is useful if you want to build pivot tables or charts in Google Sheets alongside the built-in Dinary analytics.

### How it works

- Each `POST /api/expenses` appends a row to the **first worksheet** of the configured spreadsheet.
- The 3D category/event/tags are projected to 2D `(sheet_category, sheet_group)` via the `logging_mapping` table. If no mapping exists for a category, the category name is used as a fallback.
- The same worksheet may hold **multiple years** at once. Rows are sorted by `(year, month, sheet_category, sheet_group)`, with newer year/month blocks on top. The worker reads the year from column A's underlying date value (Google displays it as e.g. `Apr-1`, but the cell stores `2026-04-01`), so January 2026 and January 2027 do **not** collide.
- Each appended row carries an opaque idempotency marker `[exp:<expense_id>]` in column J. If a previous append for the same expense reached Google but the response was lost (timeout), the next attempt sees the marker and skips the duplicate write.
- If the append fails (network error, quota), the job stays in a queue and is retried on the next `inv drain-logging` sweep.

### Enabling

Set `DINARY_SHEET_LOGGING_SPREADSHEET` to the spreadsheet ID or the full browser URL:

```bash
# Either a bare spreadsheet ID:
DINARY_SHEET_LOGGING_SPREADSHEET=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms

# Or a full URL (the ID is extracted automatically):
DINARY_SHEET_LOGGING_SPREADSHEET=https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms/edit
```

The spreadsheet must be shared with the service account from step 4 (Editor role).

### Disabling

Leave `DINARY_SHEET_LOGGING_SPREADSHEET` empty or unset. Expenses are still saved to DuckDB; only the Google Sheets append is skipped.

### Draining pending jobs

If the server was restarted while sheet-append jobs were in flight, run:

```bash
inv drain-logging
```

This sweeps all `budget_*.duckdb` files and retries every pending job.
