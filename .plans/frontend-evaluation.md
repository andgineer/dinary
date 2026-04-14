# Frontend Tool Evaluation — Phase 0

## Must-Have Criteria

| # | Criterion | Description |
|---|-----------|-------------|
| 0 | No app store | Installable without App Store / Google Play |
| 1 | Offline support | Queue entries locally, sync when online |
| 2 | Cross-platform mobile | Works on Android + iOS |
| 3 | Custom REST API | Connects to our own FastAPI backend |
| 4 | Free | No per-user or monthly fees |
| 5 | No vendor lock-in | Can migrate away without rewriting backend |
| 6 | QR scanning | Camera access for receipt QR codes |

## Candidates

### Disqualified (from architecture.md)

- **Telegram Bot** — no offline support, no QR camera access.
- **Tally / Typeform** — no offline, no QR, no custom API.

### Evaluated

| Candidate | #0 | #1 | #2 | #3 | #4 | #5 | #6 | Verdict |
|-----------|----|----|----|----|----|----|----|----|
| **PWA (custom)** | Yes — Add to Home Screen | Yes — Service Workers + IndexedDB | Yes — any mobile browser | Yes — full control | Yes — zero cost | Yes — standard web tech | Yes — Camera API | **Pass** |
| **Glide Apps** | Yes | Unclear | Yes | Unclear — limited API connectors | Free tier limited | Vendor-dependent | Unclear | Likely fails #1 or #6 |
| **Retool** | Yes (web) | No — "likely none" per docs | Yes | Yes | Free tier limited | Moderate | Unclear | Fails #1 |
| **Appsmith** | Yes (self-hosted) | Unclear | Yes | Yes | Yes (self-hosted) | Low lock-in | Unclear | Unverified on #1, #6 |

## Decision

**PWA** — the only candidate that demonstrably passes all 7 must-have criteria without further investigation. The backend API is tool-agnostic, so if a low-code platform later proves viable, it can be swapped in without backend changes.

### Tech stack

- **html5-qrcode** (MIT) for QR scanning via rear camera
- **IndexedDB** for offline entry queue
- **Service Worker** for caching and background sync
- Vanilla HTML/CSS/JS — no build step, no framework
- Served by FastAPI `StaticFiles` (same origin, same Cloudflare Tunnel)
