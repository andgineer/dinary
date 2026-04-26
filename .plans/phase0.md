# Phase 0 Implementation Plan

> **Status note (2026-04):** this file is historical implementation history for
> the direct-to-Google-Sheets MVP. The live system has moved on: FastAPI now
> serves the same-origin PWA plus a DuckDB-backed API, static assets are built
> from `static/` into `_static/` via `inv build-static`, runtime configuration
> lives in `.deploy/.env`, and operator workflows are centered on `tasks.py`
> plus the single-file `data/dinary.duckdb` design documented in
> [architecture.md](architecture.md). Keep this file as Phase 0 context, not as
> current operational guidance.

## Current State

- **Phase 0 is superseded by Phase 1.** The DuckDB-backed architecture is now the active system. Phase 0 code (direct Google Sheets writes) remains in the codebase for rollback purposes but is no longer the active write path.
- **Phase 0 was previously complete and in daily use.** The MVP was a FastAPI service that served a mobile-first PWA, wrote directly to Google Sheets, supported offline-safe expense entry, and was deployed to an always-on VM.
- **dinary** (`/Users/andrei_sorokin2/projects/dinary`): production repo for the full Phase 0 stack: FastAPI backend, Google Sheets integration, QR parsing, PWA frontend in `static/`, Oracle/Tailscale deployment tasks, and unified Python + JavaScript test reporting via Allure.
- **dinary** (`/Users/andrei_sorokin2/projects/dinary`): still reserved for the future Rust desktop app. Not used in Phase 0.

### Implemented MVP summary

- **Backend**: FastAPI API writes to the existing Google Sheets workbook without changing the spreadsheet mental model.
- **Frontend**: installed PWA with two cascading dropdowns (`group -> category`), manual amount entry, live QR scan, and a queue modal for unsent expenses.
- **Offline safety**: expense is first saved to IndexedDB and only removed after confirmed server success. Network/server failures must never drop user input.
- **Deployment**: `inv setup-server` for one-time provisioning, `inv deploy` for code
  updates, `inv status --remote`, `inv logs --remote`, `inv ssh`, and `inv test`.
- **Access model**: Tailscale is the default deployment mode, using tailnet-only `tailscale serve`; `cloudflare` and `none` remain supported options.

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
- **Always-on hosting required**: Oracle Cloud Free Tier (AMD Micro or ARM) or self-hosted (Mac/PC + Tailscale Serve / Cloudflare Tunnel).

---

## Step 1: Backend -- dinary

Replace the placeholder CLI with a FastAPI service. All backend work happens in **this repo** (`dinary`).

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
- **Read categories**: on startup, read the category/group structure from the sheet (the repeating month block). Cache in memory with a 1-hour TTL. The `GET /api/categories` endpoint serves from cache; if the user adds a category in the sheet, it appears in the PWA within an hour (or on server restart).
- **Write expense**: given `(amount_rsd, category, group, comment, date)`, find the correct month block, locate the category row, and append the new amount to the existing RSD formula instead of overwriting it. Existing EUR/month formulas are preserved. If a comment column exists, append the comment rather than replacing it. **One QR scan = one category = one entry.**
- **Auto-month creation**: if no rows exist for the target month, copy the full category block from the previous month, preserve the sheet formulas, zero out the RSD totals, and insert the new month immediately after the header (top of the year), not at the bottom.
- **Exchange rate and currency conversion**: the EUR/RSD exchange rate is stored in the sheet itself, in the rate column on the first row of each month block. When writing an expense, the backend ensures the rate exists for the month and writes it only to that first row if missing. The EUR column in the expense rows remains spreadsheet-driven.

### 1.3 QR receipt parser (`services/qr_parser.py`)

Uses `sr-invoice-parser` to extract total and date from the SUF PURS page:

- Input: URL from the QR code (e.g., `https://suf.purs.gov.rs/v/?vl=...`).
- Uses `sr-invoice-parser` which fetches and parses the page.
- Returns `{ "amount": 1234.56, "date": "2026-04-14" }`.
- No line-item parsing, no store extraction in Phase 0.

### 1.4 API endpoints

- **`POST /api/expenses`** -- receives `{ amount, currency, category, group, comment, date }`, writes to Google Sheets, returns success/failure. If the Sheets write fails (network error, quota, auth expiry), returns an error status; the PWA treats this like an offline case and re-queues the entry locally for retry.
- **`POST /api/qr/parse`** -- receives `{ url }`, fetches SUF PURS, returns `{ amount, date }`. In the current MVP this is mainly a fallback/debug path because the client can extract the common amount/date fields directly from the receipt URL.
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

- **Tailscale Serve** (default) — tailnet-only access, no public exposure, best fit when the phone already uses Tailscale.
- **Cloudflare Access** (free tier, up to 50 users) — if using Cloudflare Tunnel for HTTPS.
- **None** — only for explicitly unmanaged deployments where the user secures networking separately.
- All approaches keep backend auth out of the application code; access control stays at the infrastructure layer.

**Deployment options (all free, always-on):**

- **Oracle Cloud Free Tier** — AMD Micro VM (1 OCPU, 1 GB RAM, always available) or ARM A1 (if capacity exists). Run with `uvicorn` as a systemd service (no Docker in production — saves RAM on 1 GB instances). Docker remains available for local development.
- **Self-hosted (Mac/PC)** — run locally, expose via Tailscale Serve or Cloudflare Tunnel. Aligns with Phase 4 architecture (desktop AI agent on the same machine).

**PWA serving:** FastAPI `StaticFiles` mount at `/`, source in `static/`. Single tunnel covers both API and PWA.

**Operational commands:** `inv setup-server`, `inv deploy`, `inv status --remote`, `inv logs --remote`, `inv ssh`, `inv test`.

---

## Step 2: Frontend -- PWA in dinary repo

The PWA lives in this repo (`dinary`) since the backend serves it directly via FastAPI `StaticFiles`. No cross-repo deployment step, no build-time copying — the PWA source is right next to the backend code.

The `dinary` repo (`../dinary/`) remains for the future Rust desktop app and is not used in Phase 0.

### 2.1 Repo structure (dinary additions)

```
dinary/
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
      qr-scanner.js            # Live QR scanning via zbar-wasm
      api.js                   # API client (fetch wrapper, relative URLs)
      offline-queue.js         # IndexedDB queue for offline entries
      categories.js            # Group/category dropdown wiring and defaults
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

- **Manual entry form**: amount (RSD, decimal-friendly mobile input) + group dropdown + category dropdown + optional comment + date (defaults to today). Group/category are two separate controls matching the spreadsheet model. Defaults are optimized for the most common case.
- **QR scan with parallel processing**: when the user scans a QR code, the PWA simultaneously (a) extracts data from the receipt URL path and (b) shows the form so the user can keep moving. Backend parsing remains available as an API capability, but the MVP path does not depend on a roundtrip just to pre-fill amount/date.
- **Offline queue and error resilience**: each entry is saved to IndexedDB before network send. The queue is flushed on app open, on `online`, and immediately after a successful user action when pending items exist. Failed submissions stay queued until confirmed by the server. The queue badge opens a modal with the unsent expenses and copy-to-clipboard support.
- **PWA install**: `manifest.json` + `navigator.storage.persist()` enable reliable "Add to Home Screen" on both Android and iOS Safari.
- **Mobile-first design**: large touch targets, minimal scrolling, optimized for one-handed phone use.

### 2.3 QR scanning

- Use `zbar-wasm` for live scanning in the browser. It has proven more reliable than earlier JS-only scanner attempts for dense Serbian fiscal QR codes.
- The scanner accesses the rear camera, decodes the QR code locally, and returns the URL string. This step is **fully offline** (client-side image processing).
- iOS-specific zoom/focus constraints are applied as a workaround for Safari camera limitations.
- QR scanning requires HTTPS or a trusted tailnet origin. In practice this is satisfied by Cloudflare Tunnel or Tailscale Serve.

### 2.4 User manuals (`docs/src/en/`, `docs/src/ru/`)

MkDocs-based documentation in English and Russian:

- **`pwa-install.md`** -- how to install the PWA on Android and iOS, usage guide.
- **`cloudflare-setup.md`** -- Cloudflare Tunnel creation, DNS routing, Access policy configuration.
- **`deploy-oracle.md`** -- Oracle Cloud Free Tier: account setup, AMD Micro / ARM VM, systemd service, firewall.
- **`deploy-selfhost.md`** -- Mac/PC deployment with Tailscale Serve or Cloudflare Tunnel.

---

## Step 3: Testing and Validation

- **Primary local command**: run `inv test`. It executes both pytest and Vitest and writes all results into a shared `allure-results/` directory.
- **Backend tests**: pytest covers API endpoints, Google Sheets write safety, exchange rates, month creation, helpers, and service modules.
- **PWA tests**: Vitest covers offline queue behavior and explicit no-data-loss guarantees (enqueue-before-send, retention on server/network failure, removal only after confirmed success).
- **Manual E2E test**: deploy backend, open the PWA on phone, scan a real receipt QR code, verify amount + date appear, pick group/category, submit, and confirm the correct Google Sheets row changes.
- **Offline test**: turn off Wi-Fi/mobile data, submit an entry, verify it is queued locally, restore connectivity, and verify it syncs.

### Allure reporting contract

This taxonomy is part of the MVP contract and should stay stable as new tests are added. Do not add unlabeled tests.

- **Every new test must declare an Allure epic and feature.**
- **Prefer reusing an existing epic/feature.** Introduce a new one only for a genuinely new subsystem, and update this document in the same change.
- **Python tests** use `@allure.epic(...)` and `@allure.feature(...)`.
- **JavaScript tests** use `allure.epic(...)` and `allure.feature(...)` in the relevant `describe` scope.

Current approved structure:

- **Data Safety**
- Features: `Formula Preservation`, `Comment Preservation`, `Column Protection`, `Offline Queue`, `No Data Loss`
- **Google Sheets**
- Features: `Read Categories`, `Write Expense`, `Exchange Rate`, `Month Creation`, `Helpers`
- **API**
- Features: `Health`, `Categories`, `Expenses`, `QR Parse`
- **Services**
- Features: `Category Store`, `Exchange Rate`, `QR Parser`
- **Build**
- Features: `Version`

---

## Repo Responsibility Summary

- **dinary** (this repo): FastAPI backend, Google Sheets integration, QR page parser, API, PWA frontend (in `static/`), Docker for local dev, deployment config, dev docs in `.plans/`, user manual in `docs/`.
- **dinary** (`../dinary/`): Not used in Phase 0. Reserved for the future Rust desktop app (daemon + GUI, Phase 4+).

---

## Key Risks

- **SUF PURS page structure may change** -- the QR parser is brittle by nature. Mitigated by keeping it minimal (total + date only) and caching raw HTML for debugging. Using `sr-invoice-parser` shares maintenance with the open-source community.
- **QR scanning on iOS Safari** -- even with `zbar-wasm`, Safari camera behavior remains device-sensitive. Must be tested on a real iPhone whenever scanner code changes.
- **Google Sheets rate limits** -- unlikely at 10-20 entries/day, but the offline queue + batch flush pattern helps.

## Deliberate Scope Boundaries

These are not gaps -- they are intentional Phase 0 limitations documented here for clarity:

- **One QR scan = one category = one entry.** Receipt splitting across categories is a Phase 3 feature.
- **QR decoding is client-side; backend parsing is fallback.** The QR code is decoded on the device. Common amount/date extraction happens locally in the MVP path, while the backend parser remains available for compatibility and recovery cases.
- **Category cache has 1-hour TTL.** New categories added directly in the sheet appear in the PWA within an hour or on server restart.
