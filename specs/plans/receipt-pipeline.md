# Receipt Pipeline — Implementation Plan

> Design: see "Classification Layer" and "Receipt Scanning" sections in [`architecture.md`](../architecture/architecture.md).
> Receipt fetching API research: [`receipt-fetching.md`](../reference/receipt-fetching.md).
> Free LLM provider evaluation: [`llm-providers.md`](../reference/llm-providers.md).

## Scope

Phase 2: automatic receipt classification via QR scan, background LLM classification with provider failover, expense aggregation by category, and PWA review / correction UX.

---

## POC Findings (2026-05-07)

Ran `inv classify-receipt` against real receipts before full pipeline implementation. Key findings:

### `suf.purs.gov.rs` instability
The Serbian fiscal receipt server is unreliable — timeouts (30s+) observed on repeated calls to the same URL that succeeded moments before. The drain must treat parse failures as transient and retry, not poison immediately.

- **Impact:** `POST /api/receipts` should save the raw URL and let the drain retry fetching/parsing, not fail the request if the government server is slow.
- **Action:** drain retry budget for receipt fetch failures (separate from LLM failures); do not mark job as `poisoned` on network timeout.

### LLM provider free tier quotas
Gemini API keys created via Google Cloud Console (not AI Studio) get `limit: 0` free tier quota — requests fail immediately with 429. Keys must be created at **aistudio.google.com** to get the free tier allocation.

- **Action:** distinguish `limit: 0` (bad key/project setup) from normal 429 rate limiting; surface as a provider config error in the admin UI, not a temporary backoff.

### Provider comparison (tested on 5 real receipts, 2026-05-07)

Tested the most challenging receipt (19 items: dairy × 10, KG produce, KG deli meat, protein bars × 6, clothing).

| Provider | Model | Food ✓ | Non-food ✓ | TOTAL match | Notes |
|---|---|---|---|---|---|
| **Groq** | llama-3.3-70b-versatile | ✓ | ✓ одежда | ✓ | Best quality, fastest, conf=4 throughout |
| **OpenRouter** | openai/gpt-oss-120b:free | ✓ | ✓ одежда | ✓ | Protein bar conf=3, otherwise solid |
| **OpenRouter** | nvidia/nemotron-3-super-120b-a12b:free | ✓ | ✓ одежда | ✓ | Deli meat conf=3, otherwise solid |
| **Gemini** | gemini-2.5-flash | ✓ | ✓ одежда | ✓ | Good quality; 20 RPM free tier, intermittent 503 |
| ~~Cerebras~~ | ~~llama3.1-8b~~ | ~~partial~~ | ~~✗ missed~~ | ~~✗~~ | ~~Failed to classify clothing; Qwen3-235B also rate-limits immediately. Removed.~~ |

**Seeded provider pool** (in `.deploy/.env`, priority order for round-robin):
1. Groq / llama-3.3-70b-versatile
2. OpenRouter / openai/gpt-oss-120b:free
3. OpenRouter / nvidia/nemotron-3-super-120b-a12b:free
4. Gemini / gemini-2.5-flash

**Failover strategy:** round-robin on 429/503 — move immediately to the next provider without waiting. With 4 providers and a 300s drain interval, we will rarely exhaust the pool.

### Receipt item parsing — `/specifications` endpoint

`suf.purs.gov.rs` consumer portal exposes a structured item endpoint used by their own website. It is not in the official TaxCore documentation (which covers the B2B POS API requiring certificate auth), but it is the canonical structured source used by the tax authority's own UI.

**Flow** (`src/dinary/adapters/serbian_receipt_parser.py`):
1. `GET ?vl=... Accept: application/json` → store metadata (businessName, taxId, totalAmount, invoiceNumber)
2. `GET ?vl=...` HTML → extract session token from embedded JS (`viewModel.Token(...)`)
3. `POST /specifications {invoiceNumber, token}` → JSON array of items with decimal quantities

**Item fields**: `name`, `quantity` (float — handles KG by-weight correctly), `unitPrice`, `total`, `label` (VAT rate code)

**Total validation**: `items_total = sum(item.total)` must match `totalAmount` within 0.02 RSD. Mismatch logged as warning; classification proceeds.

### `inv classify-receipt` — completed (experiment baseline)
- [x] `src/dinary/background/classification/item_normalizer.py` — `normalize_item_name()`
- [x] `src/dinary/adapters/serbian_receipt_parser.py` — `parse_receipt()` via `/specifications`
- [x] `src/dinary/adapters/llm_client.py` — `LLMClient` protocol + `OpenAICompatibleClient`
- [x] `tasks/receipt.py` — `inv classify-receipt --url ...`
- [x] Full test suite (65 tests, all passing)

### Receipt ingestion design change
**Replace** plan step 2 integration note: use `receipt_parser.parse_receipt(url)` (3-step fetch via `/specifications`) instead of `sr-invoice-parser.get_items()`. The `sr-invoice-parser` dependency is kept only for its exception types and URL validation.

### Remaining normalizer gaps (low priority — LLM handles correctly)

| Issue | Example pattern |
|---|---|
| Size token in middle of name | `VODA NEGAZIRANA 6L MOJ D KOM` |
| Non-unit variant code in suffix | `Sladoled.MK4/7005486` |
| Unit followed by non-unit suffix | `Jagode 500g pak.` |

---

## Implementation Status

### 1. DB Migration — ✅ Done
- `src/dinary/migrations/0004_receipt_pipeline.sql`
- Tables: `stores`, `receipts`, `receipt_items`, `classification_rules`, `receipt_classification_jobs`, `llm_providers`, `llm_call_log`
- `expenses` altered: added `receipt_id`, `store_id`, `confidence_level`
- Tests: migration applies cleanly on fresh DB and existing Phase-1 DB

### 2. Receipt Ingestion — `POST /api/receipts` — ✅ Done
- `src/dinary/api/receipts.py`
- Idempotent via `client_receipt_id` UNIQUE constraint (returns `status="duplicate"` on replay)
- Single transaction: INSERT `receipts` + `receipt_items` + `receipt_classification_jobs`
- Returns 200 immediately; no classification on hot path
- `_write_fetch_fallback_metadata()` called when drain uses journal fallback (writes `receipt_fetch_fallback_last` / `receipt_fetch_fallback_count`; cleared when `/specifications` succeeds)
- Offline IndexedDB queue in PWA (`webapp/src/stores/receiptQueue.js`, `webapp/src/api/receipts.js`)
- Tests: `tests/api/test_api_receipts.py`, `webapp/tests/api-receipts.test.js`

### 3. Confidence Level Design — ✅ Done
All four levels implemented as specified. Penalties (journal fallback: −1, failover: −1, floor=1) applied in `receipt_classification_task.py`. Expense `confidence_level = MIN(item confidence)`.

### 4. Item Name Normalisation — ✅ Done
- `src/dinary/background/classification/item_normalizer.py` — `normalize_item_name()`
- Strips trailing weight/volume/pack tokens, barcode suffixes, VAT codes; lowercases; collapses whitespace

### 4. Classification Rules Engine — ✅ Done
- `src/dinary/taxonomy/classification_rules.py`
- `RuleSpec(category_id, confidence_level, source)` dataclass
- `classify_by_rules(conn, store_id, item_name_normalized)` → chain-specific beats generic
- `create_or_update_rule(conn, store_id, item_name_normalized, spec: RuleSpec)` — upsert; user_correction always sets conf=4
- Tests: `tests/services/test_classification_rules.py`

### 5. LLM Provider Abstraction — ✅ Done
- `src/dinary/adapters/llm_client.py`
- `LLMClient` protocol, `OpenAICompatibleClient`, `ProviderPool`, `AllProvidersExhausted`, `ReceiptContext`
- Round-robin failover on 429/503; `rate_limited_until` tracked per provider
- `app_metadata` keys written: `llm_provider_switch_last`, `llm_provider_switch_count`, `llm_all_exhausted_last`; both switch keys cleared on primary-provider success
- `llm_call_log` written on every call (provider_id, receipt_id, status, latency_ms)
- Env bootstrap: `src/dinary/adapters/llm_bootstrap.py` — `seed_llm_provider_if_empty()` called from `init_db()` if `DINARY_LLM_BASE_URL` + `DINARY_LLM_API_KEY` set and table is empty
- Tests: `tests/services/test_llm_client.py`

### 6. Store Resolution — ✅ Done
- `src/dinary/background/classification/store_resolver.py` — `async resolve_store(conn, pool, store_pib, store_name_raw) → int | None`
- PIB cache → LLM chain-name call → chain lookup (UPDATE pib if match) → INSERT new store

### 7. Classification Drain — ✅ Done
- `src/dinary/background/receipt_classification_task.py`
- Full drain cycle: parse → store resolution → normalise → rules lookup → LLM batch call → penalties → aggregate by category → INSERT expenses → UPDATE receipt_items → upsert rules → trim llm_call_log → complete job
- Level-1 items: no expense created, `expense_id = NULL`
- Circuit breaker: exponential backoff (60s → 1800s) on `AllProvidersExhausted`; reset on success
- Poison on permanent error (e.g. `ParserParseException`); release for retry on transient (network timeout)
- Tests: `tests/tasks/test_receipt_drain.py`, `tests/services/test_receipt_classification.py`, `tests/api/test_receipt_pipeline_e2e.py`

### 8. LLM Admin API — ✅ Done
- `src/dinary/api/llm.py + src/dinary/api/controllers/llm.py`
- `GET /api/llm/providers` — list all
- `POST /api/llm/providers` — add provider
- `PATCH /api/llm/providers/{id}` — update label / model / api_key / priority / is_enabled
- `DELETE /api/llm/providers/{id}` — refuse if only enabled provider
- `POST /api/llm/providers/{id}/test` — test classification call
- `GET /api/llm/status` — usage stats + `rate_limited_until`
- Tests: `tests/api/test_admin_llm.py`

### 9. User Correction API — ✅ Done
- `src/dinary/api/expense_corrections.py`
- `PATCH /api/expenses/{id}/category`
  1. Update `expenses.category_id`, `confidence_level = 4`
  2. Update linked `receipt_items.category_id`, `confidence_level = 4`
  3. Upsert `classification_rules` (source='user_correction', conf=4)
  4. Find all other `receipt_items` with same `(store_id, name_normalized)` → update + upsert rules
  5. Split/merge parent expenses for moved items (handles partial-move case)
  6. Returns `{corrected_expense_id, batch_updated_count}`
- Tests: `tests/api/test_receipt_pipeline_e2e.py`

### 10. Review API — ✅ Done
- `src/dinary/api/rules.py + src/dinary/api/controllers/rules.py`
- `GET /api/rules/feed?page=N&page_size=20` — two-block paginated feed:
  - Block 1 (doubtful, conf < 4): deduplicated by `(store_id, item_name_normalized)`, sorted by `SUM(total_price) DESC`
  - Block 2 (certain, conf = 4): all receipt expenses, sorted by receipt datetime DESC
  - Response: `{doubtful_count, items: [...], has_more}` with `is_doubtful: bool` per item
- `GET /api/rules/counts` — `{doubtful_rules: int}` for PWA badge
- Tests: `tests/api/test_receipt_review.py`

### 11. PWA Changes — ⚠️ Partial
- [x] `POST /api/receipts` called on QR scan (replacing `POST /api/expenses`)
- [x] Offline IndexedDB queue for receipts (`webapp/src/stores/receiptQueue.js`)
- [x] Flush composable (`webapp/src/composables/flushReceiptQueue.js`)
- [x] Vitest: `webapp/tests/api-receipts.test.js` covers `postReceipt` API layer
- [ ] **Review screen** — not yet built. Backend APIs (`/feed`, `/counts`, `PATCH /category`) are fully implemented and tested. Frontend integration is the remaining work:
  - Infinite-scroll feed component (doubtful items highlighted, visual separator, badge on nav)
  - Correction UX: tap item → category picker → `PATCH /api/expenses/{id}/category` → refresh + badge decrement
  - Vitest tests for review/correction components
- [ ] **LLM status screen** — not yet built. Backend API (`/api/llm/status`, full CRUD) is done. Frontend: provider list, usage bars, add/edit/test, enable/disable.

### 12. `inv reclassify-receipts` — ✅ Done
- `tasks/receipt.py` — `reclassify_receipts(c, receipt_id=None, from_date=None, clear_rules=False, yes=False)`
- Flags: `--receipt-id`, `--from-date`, `--clear-rules`, `--yes`
  - Note: flag is `--from-date` (not `--from` as originally planned)
- `receipt_repo.requeue_receipts()`: clears `receipt_items` classification fields → deletes `expenses WHERE receipt_id IN (...)` → optionally deletes matching `classification_rules` → re-inserts into `receipt_classification_jobs ON CONFLICT DO NOTHING`
- Tests: `tests/tasks/test_reclassify_receipts.py` — covers delete/reset/requeue, idempotency, `--clear-rules`

### 13. `inv healthcheck` Integration — ✅ Done
- `tasks/healthcheck.py` — `_healthcheck_receipt_llm()` and `_healthcheck_receipt_fetch()`
- LLM checks: `llm_provider_switch_last` (FAIL + details), `llm_all_exhausted_last` (FAIL), `llm_provider_switch_count` (info)
- Fetch checks: `receipt_fetch_fallback_last` (FAIL + details), `receipt_fetch_fallback_count` (info)
- Both integrated into `healthcheck` task (local + remote)
- Tests: `tests/tasks/test_tasks_server_receipt.py`

---

## Remaining Work

### Frontend only (backend complete + tested)

| Item | Priority | Effort |
|------|----------|--------|
| **PWA review screen** — infinite-scroll feed, doubtful items highlighted, visual block separator, nav badge = `doubtful_rules` count | High | Medium |
| **Correction UX** — tap item → category picker → `PATCH /api/expenses/{id}/category` → screen refresh + badge decrement | High | Small |
| **LLM status screen** — provider list with rate-limit state, add/edit/test/enable provider | Medium | Medium |
| **Vitest for review + correction components** | Medium | Small |

### Minor gaps

- `--from-date` flag name (task uses) vs `--from` (plan says) — cosmetic, both work fine; update plan wording ✅ (done above)
- No automated test for the `inv reclassify-receipts` CLI wrapper itself (only `requeue_receipts()` is tested directly) — low risk since the CLI is 20 lines of plumbing over a well-tested function

---

## Implementation Order (original, for reference)

| Step | Blocks |
|------|--------|
| 1. Migration | everything |
| 2. Normalisation + rules engine | drain |
| 3. LLM abstraction + store resolution | drain |
| 4. POST /api/receipts | drain, PWA |
| 5. Classification drain | correction, review APIs |
| 6. healthcheck integration | — |
| 7. LLM admin API | PWA settings |
| 8. Correction API | PWA correction UX |
| 9. Review API | PWA review screen |
| 10. PWA | — |
