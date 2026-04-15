# Phase 0 Implementation Plan

## Current State

- **dinary-server** (`/Users/andrei_sorokin2/projects/dinary-server`): FastAPI backend with Google Sheets integration, QR parser, PWA frontend, pytest suite (31 tests passing). Phase 0 backend is implemented.
- **dinary** (`/Users/andrei_sorokin2/projects/dinary`): Empty repo with only `README.md` and `.idea/`. Not used in Phase 0.

---

## Step 0: Frontend Tool Decision

Before implementation, resolve the frontend tool choice. Applying the 7 must-have criteria from [architecture.md](architecture.md) to the non-disqualified candidates:

- **Glide Apps** -- has offline mode and Google Sheets backend, but can't write to our custom sheet format (month blocks, running totals). Would need a separate "input" sheet + processing. Limited free tier.
- **Retool** -- no offline support. Fails #1.
- **Appsmith** -- no real offline support. Fails #1.
- **PWA (custom)** -- passes all 7: no app store (#0), offline via Service Workers + IndexedDB (#1), works on Android + iOS browsers (#2), full API control (#3), free (#4), no vendor dependency (#5), Camera API for QR (#6).

**Decision:** PWA. It is the only candidate that passes all must-haves. See `.plans/frontend-evaluation.md` for details.

### Data reliability

IndexedDB is reliable for **installed PWAs** (added to home screen):

- iOS Safari's 7-day storage eviction applies only to non-installed sites, not to home-screen PWAs.
- Android Chrome treats installed PWAs as first-class apps with persistent storage.
- `navigator.storage.persist()` provides additional protection against eviction.
- Data persists in IndexedDB until **confirmed synced** to the server.

### Always-on server requirement

The PWA on iOS cannot execute code in the background (no Background App Refresh, no Background Tasks). Sync only runs while the app is in the foreground. Therefore:

- The server **must** respond within 1-2 seconds (while the user is still in the app after saving).
- Sleeping/serverless hosting with 15-30 second cold starts is **not acceptable** — sync would fail before the server wakes up.
- **Always-on hosting required**: Oracle Cloud Free Tier (AMD Micro or ARM) or self-hosted (Mac/PC + Tailscale Funnel / Cloudflare Tunnel).

---

## Step 1: Backend -- dinary-server

Replace the placeholder CLI with a FastAPI service. All backend work happens in **this repo** (`dinary-server`).

**Why FastAPI rather than a serverless function:** FastAPI carries forward into Phase 1 (DuckDB), Phase 4 (AI agent API). A serverless backend (Apps Script, Cloudflare Workers) would be a dead end — it cannot evolve into DuckDB storage or serve the desktop AI agent.

### 1.1 Project restructure

- Replace the Click CLI in `src/dinary/main.py` with a FastAPI application.
- Update `pyproject.toml`: swap `click`/`rich-click` for `fastapi`, `uvicorn`, and add `gspread`, `httpx`, `sr-invoice-parser`, `google-auth`, `pydantic-settings`.
- Keep the existing build/test/CI scaffolding (pytest, ruff, pre-commit).

Resulting layout:

```
src/dinary/
  __init__.py
  __about__.py
  main.py              # FastAPI app factory, uvicorn entry point
  api/
    __init__.py
    expenses.py        # POST /api/expenses
    qr.py              # POST /api/qr/parse
    categories.py      # GET /api/categories
  services/
    __init__.py
    sheets.py          # Google Sheets read/write via gspread
    qr_parser.py       # Fetch SUF PURS page, extract total + date
    category_store.py  # Category/group list, auto-fill group from category
    exchange_rate.py   # Fetch NBS middle rate from kurs.resenje.org
  config.py            # Settings: sheet ID, credentials path, etc.
```

### 1.2 Google Sheets integration (`services/sheets.py`)

- Auth via **service account** (JSON key file). The spreadsheet is shared with the service account email.
- Use `gspread` to read/write.
- **Read categories**: on startup, read the category/group structure from the sheet (the ~33 category rows that repeat each month). Cache in memory with a 1-hour TTL. The `GET /api/categories` endpoint serves from cache; if the user adds a category in the sheet, it appears in the PWA within an hour (or on server restart).
- **Write expense**: given (amount_rsd, category, group, comment, date), find the correct month block in the sheet, locate the category row, add the amount to the existing RSD value (running total). If a comment column exists, append the comment. **One QR scan = one category = one entry.** If the user wants to split a receipt across categories (e.g., 3000 RSD food + 500 RSD household), they ignore the QR total and submit two separate manual entries. Receipt splitting is a Phase 3 concern.
- **Auto-month creation**: if no rows exist for the target month, copy the full category block from the previous month, update the date column to the new month's first day, zero out amounts.
- **Exchange rate and currency conversion**: the EUR/RSD exchange rate is stored in the Google Sheet itself, in a designated cell to the right of the first row of each month block. When writing an expense, the backend checks the rate cell for that month: if empty, it fetches the current NBS middle rate from `https://kurs.resenje.org/api/v1/currencies/eur/rates/{date}` (same API used in `../ibkr-porez-py/`, field `exchange_middle`) and writes it into the cell. If already filled, it uses the stored rate. This way each month has its own rate visible in the sheet, and the rate is fetched only once per month. The EUR amount is then calculated as `amount_rsd * (1 / rate)` (since the NBS rate is "1 EUR = N RSD").

### 1.3 QR receipt parser (`services/qr_parser.py`)

Uses `sr-invoice-parser` to extract total and date from the SUF PURS page:

- Input: URL from the QR code (e.g., `https://suf.purs.gov.rs/v/?vl=...`).
- Uses `sr-invoice-parser` which fetches and parses the page.
- Returns `{ "amount": 1234.56, "date": "2026-04-14" }`.
- No line-item parsing, no store extraction in Phase 0.

### 1.4 API endpoints

- **`POST /api/expenses`** -- receives `{ amount, currency, category, comment, date }`, writes to Google Sheets, returns success/failure. If the Sheets write fails (network error, quota, auth expiry), returns an error status; the PWA treats this like an offline case and re-queues the entry locally for retry.
- **`POST /api/qr/parse`** -- receives `{ url }`, fetches SUF PURS, returns `{ amount, date }`. Does not write anything -- the client uses the returned data to pre-fill the form, then submits via `/api/expenses`.
- **`GET /api/categories`** -- returns the list of categories with their groups, read from the sheet (cached).
- **`GET /api/health`** -- basic health check.
- No CORS middleware needed — the PWA is served by the same FastAPI instance (same origin), so all API calls are same-origin requests.
- **Authentication via Cloudflare Access or Tailscale** (see section 1.7). No auth logic in the backend code itself.

### 1.5 Logging

Structured logging to stdout via Python `logging` (JSON format for production). All API errors (Sheets write failures, SUF PURS fetch errors, malformed QR URLs) are logged with context. HTTP error responses include a meaningful `detail` message that the PWA can display to the user.

### 1.6 Configuration

- `config.py` using Pydantic Settings (env vars with `DINARY_` prefix):
  - `DINARY_GOOGLE_SHEETS_CREDENTIALS_PATH` -- path to service account JSON
  - `DINARY_GOOGLE_SHEETS_SPREADSHEET_ID` -- the sheet ID

### 1.7 Authentication & Deployment

**Authentication options:**

- **Cloudflare Access** (free tier, up to 50 users) — if using Cloudflare Tunnel for HTTPS.
- **Tailscale Funnel** — URL is not publicly discoverable; optionally add a simple bearer token check.
- Both approaches keep the backend auth-free — authentication is handled at the infrastructure layer.

**Deployment options (all free, always-on):**

- **Oracle Cloud Free Tier** — AMD Micro VM (1 OCPU, 1 GB RAM, always available) or ARM A1 (if capacity exists). Run with `uvicorn` as a systemd service (no Docker — saves RAM on 1 GB instances). Docker is available for local development.
- **Self-hosted (Mac/PC)** — run locally, expose via Tailscale Funnel or Cloudflare Tunnel. Aligns with Phase 4 architecture (desktop AI agent on the same machine).

**PWA serving:** FastAPI `StaticFiles` mount at `/`, source in `static/`. Single tunnel covers both API and PWA.

---

## Step 2: Frontend -- PWA in dinary-server repo

The PWA lives in this repo (`dinary-server`) since the backend serves it directly via FastAPI `StaticFiles`. No cross-repo deployment step, no build-time copying — the PWA source is right next to the backend code.

The `dinary` repo (`../dinary/`) remains for the future Rust desktop app and is not used in Phase 0.

### 2.1 Repo structure (dinary-server additions)

```
dinary-server/
  src/dinary/
    ...                        # Backend (see Step 1)
  static/                      # PWA source, served by FastAPI StaticFiles
    index.html
    manifest.json              # PWA manifest (name, icons, display: standalone)
    sw.js                      # Service Worker (offline caching + background sync)
    css/
      style.css                # Mobile-first responsive styles
    js/
      app.js                   # Main app logic
      qr-scanner.js            # QR scanning via html5-qrcode library
      api.js                   # API client (fetch wrapper, relative URLs)
      offline-queue.js         # IndexedDB queue for offline entries
      categories.js            # Category list + group auto-fill
    icons/                     # PWA icons (192x192, 512x512)
  .plans/
    frontend-evaluation.md     # Tool evaluation summary + decision
  docs/src/en/                 # MkDocs user manuals (English)
    pwa-install.md
    cloudflare-setup.md
    deploy-oracle.md
    deploy-selfhost.md
  docs/src/ru/                 # MkDocs user manuals (Russian)
```

### 2.2 PWA features

- **Manual entry form**: amount (RSD, numeric keyboard) + category dropdown (grouped by group, ~33 items) + group (auto-filled, read-only) + optional comment + date (defaults to today). Single "Save" button.
- **QR scan with parallel processing**: when the user scans a QR code, the PWA simultaneously (a) sends the URL to the server for parsing and (b) shows the category selection form. While the user picks a category (2-5 seconds), the server response may arrive — if so, the amount and date are shown in the form as confirmation. If the response doesn't arrive (offline or slow), the user can still save with just the URL and category; the server extracts the amount during sync.
- **Offline queue and error resilience**: when offline, completed entries are stored in IndexedDB. The PWA flushes the queue to the backend when connectivity returns (on `online` event or on app open). If the backend returns an error on submission, the PWA re-queues the entry for automatic retry. Visual indicator shows queued entries count and sync status per entry (`pending` → `syncing` → `synced`).
- **PWA install**: `manifest.json` + `navigator.storage.persist()` enable reliable "Add to Home Screen" on both Android and iOS Safari.
- **Mobile-first design**: large touch targets, minimal scrolling, optimized for one-handed phone use.

### 2.3 QR scanning

- Use `html5-qrcode` (MIT license, widely used, works on Android Chrome and iOS Safari).
- The library accesses the rear camera, decodes the QR code, and returns the URL string. This step is **fully offline** (client-side image processing).
- The URL is sent to the backend for parsing. If offline, the URL is saved with the entry and parsed during sync.
- QR scanning requires HTTPS (browser camera API constraint). This is satisfied by Cloudflare Tunnel or Tailscale Funnel.

### 2.4 User manuals (`docs/src/en/`, `docs/src/ru/`)

MkDocs-based documentation in English and Russian:

- **`pwa-install.md`** -- how to install the PWA on Android and iOS, usage guide.
- **`cloudflare-setup.md`** -- Cloudflare Tunnel creation, DNS routing, Access policy configuration.
- **`deploy-oracle.md`** -- Oracle Cloud Free Tier: account setup, AMD Micro / ARM VM, systemd service, firewall.
- **`deploy-selfhost.md`** -- Mac/PC deployment with Tailscale Funnel or Cloudflare Tunnel.

---

## Step 3: Testing and Validation

- **Backend tests**: pytest tests for each endpoint (mock gspread and httpx for external calls). Test auto-month creation logic. Test QR parser against a saved sample HTML page. Run `inv pre` (ruff, pyrefly) and `uv run pytest` after every change.
- **PWA tests**: vitest tests for offline queue and no-data-loss guarantees (expense always saved to IndexedDB before network call, removed only after confirmed 200, survives server errors/timeouts). Run `npm test`.
- **Manual E2E test**: deploy backend, open PWA on phone, scan a real receipt QR code, verify amount + date appear, pick category, submit, check Google Sheets row is created.
- **Offline test**: turn off Wi-Fi, submit an entry, verify it is queued locally, turn Wi-Fi back on, verify it syncs.

---

## Repo Responsibility Summary

- **dinary-server** (this repo): FastAPI backend, Google Sheets integration, QR page parser, API, PWA frontend (in `static/`), Docker for local dev, deployment config, dev docs in `.plans/`, user manual in `docs/`.
- **dinary** (`../dinary/`): Not used in Phase 0. Reserved for the future Rust desktop app (daemon + GUI, Phase 4+).

---

## Key Risks

- **SUF PURS page structure may change** -- the QR parser is brittle by nature. Mitigated by keeping it minimal (total + date only) and caching raw HTML for debugging. Using `sr-invoice-parser` shares maintenance with the open-source community.
- **QR scanning on iOS Safari** -- `html5-qrcode` works on iOS Safari but historically has had quirks. Must be tested on a real iPhone early.
- **Google Sheets rate limits** -- unlikely at 10-20 entries/day, but the offline queue + batch flush pattern helps.

## Deliberate Scope Boundaries

These are not gaps -- they are intentional Phase 0 limitations documented here for clarity:

- **One QR scan = one category = one entry.** Receipt splitting across categories is a Phase 3 feature.
- **QR URL extraction is offline; amount extraction requires server.** The QR code is decoded on the device (offline). The URL is saved with the entry. Amount and date are extracted by the server during sync if unavailable at scan time.
- **Category cache has 1-hour TTL.** New categories added directly in the sheet appear in the PWA within an hour or on server restart.
