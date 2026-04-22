# Frontend Status and Evaluation

> **Status note (2026-04):** the frontend decision is no longer hypothetical.
> The custom PWA is the production/mobile client in this repo. Source files
> live in `static/`, deployment builds are emitted to `_static/` by
> `inv build-static`, and FastAPI serves the resulting assets from the same
> origin as the API. This file keeps the original evaluation result, but now
> also documents the currently shipped frontend surface.

## Current Shipped Frontend

- **Platform:** custom installable PWA, same-origin with the FastAPI backend.
- **Source layout:** `static/index.html`, `static/css/style.css`, and vanilla
  JS modules in `static/js/`.
- **Offline contract:** IndexedDB-backed queue in `static/js/offline-queue.js`;
  entries are persisted locally before network send.
- **QR flow:** browser camera scanning via `zbar-wasm`; the client can extract
  amount/date from Serbian fiscal QR URLs locally and still has backend QR
  parsing as a fallback.
- **Catalog model:** the live UI is already beyond the original Phase 0 shape.
  It consumes `GET /api/catalog`, posts 3D ids to `POST /api/expenses`
  (`category_id`, optional `event_id`, `tag_ids[]`), caches the catalog by
  `catalog_version`, and supports inactive-row management plus inline add/edit
  flows for groups, categories, events, and tags through `/api/admin/catalog/*`.
- **Deployment packaging:** `inv build-static` copies `static/` to `_static/`,
  substitutes `__VERSION__`, and writes `data/.deployed_version`; the server
  exposes the deployed version at `/api/version`.
- **Tests:** Vitest (`npm test`) covers the frontend modules; `inv test` runs
  both pytest and Vitest.

## Decision Criteria

| # | Criterion | Description |
|---|-----------|-------------|
| 0 | No app store | Installable without App Store / Google Play |
| 1 | Offline support | Queue entries locally, sync when online |
| 2 | Cross-platform mobile | Works on Android + iOS |
| 3 | Custom REST API | Connects to our own FastAPI backend |
| 4 | Free | No per-user or monthly fees |
| 5 | No vendor lock-in | Can migrate away without rewriting backend |
| 6 | QR scanning | Camera access for receipt QR codes |

## Candidate Evaluation (Historical)

### Disqualified

- **Telegram Bot** — no offline support, no QR camera access.
- **Tally / Typeform** — no offline, no QR, no custom API.

### Evaluated

| Candidate | #0 | #1 | #2 | #3 | #4 | #5 | #6 | Verdict |
|-----------|----|----|----|----|----|----|----|----|
| **PWA (custom)** | Yes — Add to Home Screen | Yes — Service Workers + IndexedDB | Yes — any mobile browser | Yes — full control | Yes — zero cost | Yes — standard web tech | Yes — Camera API | **Pass** |
| **Glide Apps** | Yes | Unclear | Yes | Unclear — limited API connectors | Free tier limited | Vendor-dependent | Unclear | Likely fails #1 or #6 |
| **Retool** | Yes (web) | No — "likely none" per docs | Yes | Yes | Free tier limited | Moderate | Unclear | Fails #1 |
| **Appsmith** | Yes (self-hosted) | Unclear | Yes | Yes | Yes (self-hosted) | Low lock-in | Unclear | Unverified on #1, #6 |

## Outcome

**PWA** remains the correct choice. It is still the only candidate that
cleanly satisfies the hard constraints while fitting the current codebase:

- offline-safe local persistence,
- cross-platform mobile delivery with no store review,
- camera-based QR scanning,
- direct integration with the project's own FastAPI API,
- zero dependency on a third-party app-builder runtime.

## Current Tech Stack

- **`zbar-wasm`** for live QR scanning in the browser.
- **IndexedDB** for the offline entry queue.
- **Service Worker** for installability and asset caching.
- **Vanilla HTML/CSS/JS** with no frontend framework or separate SPA toolchain.
- **FastAPI `StaticFiles`** for same-origin serving behind Tailscale or Cloudflare.
