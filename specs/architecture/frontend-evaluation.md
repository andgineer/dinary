# Frontend Status and Evaluation

> **Status note (2026-05):** the frontend decision is no longer hypothetical.
> The custom PWA is the production/mobile client in this repo. After the
> vanilla-JS prototype reached its complexity ceiling, it was rewritten in
> Vue 3 + Pinia under `webapp/` and is built into `_static/` by Vite +
> `vite-plugin-pwa` (see [`vue-refactor.md`](../plans/vue-refactor-done.md)). FastAPI
> serves the resulting assets from the same origin as the API. This file
> keeps the original evaluation result, but now also documents the
> currently shipped frontend surface.

## Current Shipped Frontend

- **Platform:** custom installable PWA, same-origin with the FastAPI backend.
- **Stack:** Vue 3 (Composition API) + Pinia + Vite + `vite-plugin-pwa`
  (Workbox-based service worker with `registerType: 'autoUpdate'`,
  `skipWaiting`, `clientsClaim`, and `NetworkOnly` for `/api/*`).
- **Source layout:** `webapp/src/` (entry `webapp/src/main.js`, root
  component `webapp/src/App.vue`, components/modals/stores/composables in
  the usual subfolders), Vite config in `webapp/vite.config.js`. Deployment
  build output is `_static/` at the repo root (gitignored).
- **Offline contract:** Pinia-backed queue persisted to IndexedDB
  (`dinary-v2`) via `webapp/src/db.js`; entries are written locally first
  and only removed after confirmed server success. The legacy `dinary`
  IndexedDB database from the vanilla-JS app is purged at boot.
- **QR flow:** browser camera scanning via `zbar-wasm`, mounted in
  `webapp/src/components/QrScanner.vue`. Amount/date can be extracted from
  Serbian fiscal QR URLs locally; the backend QR endpoint remains as a
  fallback.
- **Catalog model:** consumes `GET /api/catalog`, posts 3D ids to
  `POST /api/expenses` (`category_id`, optional `event_id`, `tag_ids[]`),
  caches the catalog by `catalog_version`, and supports inactive-row
  management plus inline add/edit flows for groups, categories, events,
  and tags through `/api/catalog/*`.
- **Currency model:** the PWA owns its own currency picker state via
  `useCurrencyStore` (saved codes + last-used). It talks to
  `GET / POST / DELETE /api/currencies` and exposes a `CurrencyPicker`
  next to the amount field. The PWA does **not** fetch exchange rates;
  conversion to the accounting currency is the server's responsibility
  and runs inside `POST /api/expenses` at write time.
- **Deployment packaging:** `inv build-static` runs
  `npm --prefix webapp ci` + `npm --prefix webapp run build`, which emits
  hashed assets and a generated service worker into `_static/`, then
  writes the deployed git short hash into `data/.deployed_version` and
  `_static/version.json`; the server exposes the deployed version at
  `/api/version`.
- **Tests:** Vitest (`npm --prefix webapp test`) covers components,
  modals, stores, and composables. `inv test` runs both pytest and
  Vitest.

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
