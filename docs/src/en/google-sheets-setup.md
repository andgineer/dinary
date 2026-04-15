# Google Sheets Setup

Dinary-server stores expenses in a Google Sheets spreadsheet. You need a Google service account
and a spreadsheet shared with that account.

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
| `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` | the spreadsheet ID from step 5 |
| `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` | path to `credentials.json` (default: `credentials.json` in the working directory) |

!!! note
    When using `docker compose` with `.env`, set `GOOGLE_SHEETS_SPREADSHEET_ID` (without the `DINARY_` prefix) — `docker-compose.yml` adds the prefix automatically. When running directly (Oracle VM, local dev), set `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID`.
