# Phase 0 Implementation Plan

## Current State

- **dinary-server** (`/Users/andrei_sorokin2/projects/dinary-server`): Python 3.13 scaffold with a placeholder Click CLI, Hatchling build, no HTTP server or business logic. 6 commits.
- **dinary** (`/Users/andrei_sorokin2/projects/dinary`): Empty repo with only `README.md` and `.idea/`. 1 commit.

---

## Step 0: Frontend Tool Decision

Before implementation, resolve the frontend tool choice. Applying the 7 must-have criteria from [architecture.md](architecture.md) to the non-disqualified candidates:

- **Glide Apps** -- unclear offline support, unclear QR scanning, unclear custom REST API connectivity. Needs investigation but likely fails #1 or #6.
- **Retool** -- "likely none" offline support (noted in own description). Probably fails #1.
- **Appsmith** -- unclear offline, unclear QR scanning. Self-hostable is nice but unverified on must-haves.
- **PWA (custom)** -- passes all 7: no app store (#0), offline via Service Workers + IndexedDB (#1), works on Android + iOS browsers (#2), full API control (#3), free (#4), no vendor dependency (#5), Camera API for QR (#6).

**Recommendation:** Start with PWA. It is the only candidate that demonstrably passes all must-haves without further research. If a quick check of Glide/Appsmith reveals they also pass, the backend API is tool-agnostic and can serve any frontend.

**Action:** Create a brief evaluation document in `.plans/frontend-evaluation.md` summarizing the decision before writing code.

---

## Step 1: Backend -- dinary-server

Replace the placeholder CLI with a FastAPI service. All backend work happens in **this repo** (`dinary-server`).

**Why FastAPI rather than a serverless function:** The architecture doc describes the Phase 0 backend as "a lightweight backend (Python script or serverless function)". A serverless function (e.g., Google Cloud Function) would avoid infrastructure management but would need to be rewritten for Phase 1, which requires a persistent FastAPI server with DuckDB. Starting with FastAPI avoids throwaway work — it is lightweight enough for Phase 0 and carries forward directly into Phase 1.

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

Minimal parser for Phase 0 -- extracts only total and date from the SUF PURS page:

- Input: URL from the QR code (e.g., `https://suf.purs.gov.rs/v/?vl=...`).
- Fetch the HTML page with `httpx`.
- Parse with `beautifulsoup4`: extract the total amount and receipt date from the page structure.
- Return `{ "amount": 1234.56, "date": "2026-04-14" }`.
- No line-item parsing, no store extraction.
- **Check `sr-invoice-parser` first:** before writing a custom parser, evaluate the existing [Innovigo/sr-invoice-parser](https://github.com/Innovigo/sr-invoice-parser) library (referenced in the architecture doc). If it already exposes total + date fields, use it directly — this avoids duplicating parsing logic and provides a smooth upgrade path to Phase 2 (which needs line-item parsing from the same library). Fall back to a custom `beautifulsoup4` parser only if `sr-invoice-parser` is unsuitable (e.g., unmaintained, missing total/date extraction, or heavy dependencies).

### 1.4 API endpoints

- **`POST /api/expenses`** -- receives `{ amount, currency, category, comment, date }`, writes to Google Sheets, returns success/failure. If the Sheets write fails (network error, quota, auth expiry), returns an error status; the PWA treats this like an offline case and re-queues the entry locally for retry.
- **`POST /api/qr/parse`** -- receives `{ url }`, fetches SUF PURS, returns `{ amount, date }`. Does not write anything -- the client uses the returned data to pre-fill the form, then submits via `/api/expenses`.
- **`GET /api/categories`** -- returns the list of categories with their groups, read from the sheet (cached).
- **`GET /api/health`** -- basic health check.
- No CORS middleware needed — the PWA is served by the same FastAPI instance (same origin), so all API calls are same-origin requests.
- **Authentication via Cloudflare Access** (see section 1.7). No auth logic in the backend code itself — Cloudflare blocks unauthenticated requests before they reach FastAPI. The backend has no API key, no auth middleware, no token validation. This is the simplest approach and keeps the PWA secret-free.

### 1.5 Logging

Structured logging to stdout via Python `logging` (JSON format for production). All API errors (Sheets write failures, SUF PURS fetch errors, malformed QR URLs) are logged with context. In Docker, logs are captured by `docker logs`. HTTP error responses include a meaningful `detail` message that the PWA can display to the user (e.g., "Google Sheets write failed, entry queued for retry").

### 1.6 Configuration

- `config.py` using Pydantic Settings (env vars or `.env` file):
  - `GOOGLE_SHEETS_CREDENTIALS_PATH` -- path to service account JSON
  - `GOOGLE_SHEETS_SPREADSHEET_ID` -- the sheet ID

### 1.7 Authentication & Deployment

**Authentication:** Cloudflare Access (free tier, up to 50 users). Both the backend API and the PWA static files are served behind Cloudflare Tunnel. Cloudflare Access is configured with a policy allowing the user's email. The flow:

1. User opens the PWA URL on their phone.
2. Cloudflare checks for a valid `CF_Authorization` session cookie.
3. If no cookie — Cloudflare shows a login page (email OTP or Google OAuth). User authenticates once.
4. Cloudflare sets a session cookie (configurable duration, e.g., 30 days) and forwards the request.
5. All subsequent PWA requests (page loads and API calls) pass through with the cookie. No login needed until the session expires.

The backend has zero auth code — no API key validation, no middleware, no token logic. Cloudflare strips unauthenticated traffic before it reaches FastAPI. The PWA has zero secrets — no config.json with API keys, no stored tokens. Authentication is handled entirely at the infrastructure layer.

**Offline queue edge case:** if the Cloudflare Access session expires while the device is offline, queued entries will get a 302/401 on the first sync attempt. The PWA detects this and prompts the user to re-authenticate (open the app in the browser to trigger the Cloudflare login flow), after which the queue flushes normally.

**Fallback if Cloudflare is undesirable:** replace with API key auth (key entered once in the PWA, stored in localStorage) or `oauth2-proxy` (self-hosted). No backend code changes needed — just swap what sits in front of uvicorn. Lock-in is shallow: Cloudflare is a deployment choice, not an application dependency.

**Deployment:**

- Dockerfile + docker-compose for easy deployment on Oracle Cloud Free Tier or any VPS.
- Backend runs with `uvicorn` behind Cloudflare Tunnel.
- PWA static files are served by the backend itself (FastAPI `StaticFiles` mount at `/`, source in `static/`). Single Tunnel covers both API and PWA.
- Deployment manual in `docs/setup.md`.

---

## Step 2: Frontend -- PWA in dinary-server repo

The PWA lives in this repo (`dinary-server`) since the backend serves it directly via FastAPI `StaticFiles`. No cross-repo deployment step, no build-time copying — the PWA source is right next to the backend code and ships in the same Docker image.

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
  docs/src/en/                   # MkDocs user manuals (English)
    pwa-install.md             # How to install PWA on phone
    cloudflare-setup.md        # Cloudflare Tunnel + Access setup
    deploy-oracle.md           # Oracle Cloud Free Tier deployment
    deploy-render.md           # Render deployment
    deploy-railway.md          # Railway deployment
  docs/src/ru/                   # MkDocs user manuals (Russian)
```

FastAPI mounts `static/` at `/` so `index.html` is served at the root URL. All API calls use relative URLs (e.g., `fetch("/api/expenses")`). Same origin — no CORS, no secrets, no config files. Authentication is handled by Cloudflare Access at the infrastructure layer — the PWA code is completely unaware of it.

### 2.2 PWA features

- **Manual entry form**: amount (RSD, numeric keyboard) + category dropdown (grouped by group, ~33 items) + group (auto-filled, read-only) + optional comment + date (defaults to today). Single "Save" button.
- **QR scan button**: opens camera via `html5-qrcode` library, scans the QR code, extracts URL, calls `POST /api/qr/parse`, pre-fills amount + date on the form. User picks category and submits.
- **Offline queue and error resilience**: when offline, completed entries are stored in IndexedDB. A Service Worker (or the app itself on `online` event) flushes the queue to the backend when connectivity returns. If the backend returns an error on submission (e.g., Sheets API failure), the PWA treats it like an offline case and re-queues the entry for automatic retry. Visual indicator shows queued entries count. **Limitation: QR scanning requires connectivity** — the backend must fetch the receipt page from the SUF PURS government server, so QR scan is unavailable offline. Offline mode covers manual entry only. This is inherent to the design (receipt data lives on a government server) and is documented in the UI (QR button disabled with "requires internet" hint when offline).
- **PWA install**: `manifest.json` enables "Add to Home Screen" on both Android and iOS Safari, providing an app-like experience without App Store publishing.
- **Mobile-first design**: large touch targets, minimal scrolling, optimized for one-handed phone use.

### 2.3 QR scanning

- Use `html5-qrcode` (MIT license, widely used, works on Android Chrome and iOS Safari).
- The library accesses the rear camera, decodes the QR code, and returns the URL string.
- The URL is sent to the backend (`POST /api/qr/parse`), which does the actual fetching and parsing.
- QR scanning requires HTTPS (browser camera API constraint). This is satisfied by the Cloudflare Tunnel deployment — all traffic is HTTPS by default.

### 2.4 User manuals (`docs/src/en/`, `docs/src/ru/`)

MkDocs-based documentation in English and Russian:

- **`pwa-install.md`** -- how to install the PWA on Android and iOS, usage guide, re-authentication.
- **`cloudflare-setup.md`** -- Cloudflare Tunnel creation, DNS routing, Access policy configuration.
- **`deploy-oracle.md`** -- Oracle Cloud Free Tier: account setup, ARM VM, Docker, firewall.
- **`deploy-render.md`** -- Render: GitHub auto-deploy, secret files, custom domain.
- **`deploy-railway.md`** -- Railway: GitHub auto-deploy, base64 credentials, usage-based pricing.

---

## Step 3: Testing and Validation

- **Backend tests**: pytest tests for each endpoint (mock gspread and httpx for external calls). Test auto-month creation logic. Test QR parser against a saved sample HTML page.
- **Manual E2E test**: deploy backend, open PWA on phone, scan a real receipt QR code, verify amount + date appear, pick category, submit, check Google Sheets row is created.
- **Offline test**: turn off Wi-Fi, submit an entry, verify it is queued locally, turn Wi-Fi back on, verify it syncs.

---

## Repo Responsibility Summary

- **dinary-server** (this repo): FastAPI backend, Google Sheets integration, QR page parser, API, PWA frontend (in `static/`), deployment config, dev docs in `.plans/`, user manual in `docs/`.
- **dinary** (`../dinary/`): Not used in Phase 0. Reserved for the future Rust desktop app (daemon + GUI, Phase 4+).

---

## Key Risks

- **SUF PURS page structure may change** -- the QR parser is brittle by nature. Mitigated by keeping it minimal (total + date only) and caching raw HTML for debugging. Using `sr-invoice-parser` (if viable) further mitigates this by sharing maintenance with the open-source community.
- **QR scanning on iOS Safari** -- `html5-qrcode` works on iOS Safari but historically has had quirks. Must be tested on a real iPhone early.
- **Google Sheets rate limits** -- unlikely at 10-20 entries/day, but the offline queue + batch flush pattern helps.

## Deliberate Scope Boundaries

These are not gaps -- they are intentional Phase 0 limitations documented here for clarity:

- **One QR scan = one category = one entry.** Receipt splitting across categories is a Phase 3 feature.
- **QR scanning requires connectivity.** The SUF PURS page lives on a government server; there is no way to parse it offline. Offline mode covers manual entry only.
- **Category cache has 1-hour TTL.** New categories added directly in the sheet appear in the PWA within an hour or on server restart. Manual cache refresh endpoint is not planned for Phase 0.
