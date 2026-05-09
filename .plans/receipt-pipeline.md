# Receipt Pipeline — Implementation Plan

> Design: see "Classification Layer" and "Receipt Scanning" sections in [`architecture.md`](./architecture.md).
> Receipt fetching API research: [`receipt-fetching.md`](./receipt-fetching.md).
> Free LLM provider evaluation: [`llm-providers.md`](./llm-providers.md).

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

**Flow** (`src/dinary/services/receipt_parser.py`):
1. `GET ?vl=... Accept: application/json` → store metadata (businessName, taxId, totalAmount, invoiceNumber)
2. `GET ?vl=...` HTML → extract session token from embedded JS (`viewModel.Token(...)`)
3. `POST /specifications {invoiceNumber, token}` → JSON array of items with decimal quantities

**Item fields**: `name`, `quantity` (float — handles KG by-weight correctly), `unitPrice`, `total`, `label` (VAT rate code)

**Total validation**: `items_total = sum(item.total)` must match `totalAmount` within 0.02 RSD. Mismatch logged as warning; classification proceeds.

### `inv classify-receipt` — completed (experiment baseline)
- [x] `src/dinary/services/item_normalizer.py` — `normalize_item_name()`
- [x] `src/dinary/services/receipt_parser.py` — `parse_receipt()` via `/specifications`
- [x] `src/dinary/services/llm_client.py` — `LLMClient` protocol + `OpenAICompatibleClient`
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

## 1. DB Migration

- [ ] Create `src/dinary/migrations/0004_receipt_pipeline.sql`
  - New tables: `stores`, `receipts`, `receipt_items`, `classification_rules`, `receipt_classification_jobs`, `llm_providers`, `llm_call_log`
  - Alter `expenses`: add `receipt_id`, `store_id`, `confidence_level`
- [ ] Unit tests: migration applies cleanly on a fresh DB and on an existing Phase-1 DB

---

## 2. Receipt Ingestion — `POST /api/receipts`

- [ ] Use `receipt_parser.parse_receipt(url)` (3-step fetch: JSON metadata → HTML token → `POST /specifications`): returns `ParsedReceipt` with structured items, decimal quantities, and total validation. PIB / store_name / total_amount / items already in the dataclass.
- [ ] **Error registration on `/specifications` fallback** — when `parse_receipt` falls back to journal parsing, write to `app_metadata`:
  - `receipt_fetch_fallback_last`: `"TIMESTAMP | invoice: INVOICE_NUMBER | reason: REASON"` — overwritten on each fallback
  - `receipt_fetch_fallback_count`: increment integer counter
  - Both cleared when `/specifications` succeeds again for a subsequent receipt
- [ ] `POST /api/receipts` endpoint:
  - Idempotent via `client_receipt_id` (`UNIQUE` constraint + payload compare, same pattern as `POST /api/expenses`)
  - Single transaction: INSERT `receipts` + `receipt_items` + `receipt_classification_jobs`
  - Returns `200 OK` immediately (no classification on hot path)
- [ ] Offline queue support in PWA IndexedDB (same pattern as manual expenses)
- [ ] Unit tests: parse real SUF PURS HTML fixture; idempotency replay; offline queue flush

---

## 3. Confidence Level Design

### Scale definition

| Level | Meaning | When assigned |
|---|---|---|
| 1 | Unresolved | LLM returned `category_id=null` or conf=1; or penalised down to 1 |
| 2 | Uncertain | LLM conf=2; or conf≥2 with penalty(ies) reducing to 2 |
| 3 | Likely | LLM conf=3 without penalty; or conf=4 with one penalty |
| 4 | Certain | LLM conf=4 without penalty; rule from `user_correction`; rule applied (no LLM call) with stored conf=4 |

### Source penalties (applied after LLM call, before storing)

- Journal fallback used instead of `/specifications`: **−1**
- Non-primary LLM provider (round-robin failover triggered): **−1**
- Penalties stack; floor is 1
- Rules-based classification (`classify_by_rules` hit): **no penalty**, returns stored confidence as-is

### Expense confidence

`expenses.confidence_level = MIN(confidence_level)` across all `receipt_items` contributing to that expense.

### Rules confidence

Rule stores the confidence level it was created with. `user_correction` always sets conf=4. LLM-sourced rules store the penalised confidence (what was actually written to the item).

---

## 4. Item Name Normalisation

- [ ] `normalize_item_name(raw: str) -> str`: lowercase, strip trailing weight/volume/pack tokens (regex: `\d+\s*(g|kg|ml|l|kom|pc)\b`), collapse whitespace
- [ ] Unit tests covering Serbian receipt edge cases (abbreviated units, Cyrillic/Latin mix)

---

## 4. Classification Rules Engine

Rule key = **(store_id, item_name_normalized)**. `store_id` is chain-level (one row per PIB in `stores`), so all physical locations of the same chain share rules. Generic rules use `store_id = NULL` and apply when no chain-specific rule exists — same item can legitimately mean different things at different chains.

- [ ] `classify_by_rules(conn, store_id, item_name_normalized) -> (category_id, confidence_level) | None`
  - SQL: `WHERE (store_id = ? OR store_id IS NULL) AND item_name_normalized = ? ORDER BY store_id NULLS LAST LIMIT 1`
  - Returns stored `confidence_level` unchanged — no source penalty (no LLM call involved)
- [ ] `create_or_update_rule(conn, store_id, item_name_normalized, category_id, confidence_level, source)`
  - `source`: `'llm'` | `'user_correction'`. User correction always sets `confidence_level = 4`.
- [ ] Unit tests: store-specific rule beats generic; miss returns None; user_correction sets conf=4

---

## 5. LLM Provider Abstraction

- [ ] `LLMClient` protocol: `classify_receipt(items: list[str], store_name_raw: str) -> list[ClassificationResult]`
  - `ClassificationResult`: `item_name_normalized`, `category_id | None`, `confidence_level: int`
- [ ] `OpenAICompatibleClient` (covers Groq, OpenRouter, Gemini via `/v1/chat/completions`)
  - `base_url` + `api_key` + `model` from `llm_providers` row
  - Structured JSON response via system prompt; parse with fallback to `confidence_level=1` on parse error
- [ ] **Confidence penalty** applied after LLM call, before storing:
  - Journal fallback used (instead of `/specifications`): `confidence -= 1`
  - Non-primary provider used (round-robin failover triggered): `confidence -= 1`
  - Penalties stack; minimum is always 1
  - Rules-based classification: no penalty (no LLM call)
- [ ] **Round-robin failover**: on 429 or 503 from provider N, immediately try provider N+1 (wrapping around). No backoff wait — move to the next at once. Surface `AllProvidersExhausted` only when the full circle completes without a successful call; keep job pending.
  - Store last-used provider index in `app_metadata` (`llm_last_provider_idx`) so the next drain iteration starts from the provider AFTER the one that last succeeded, distributing load evenly across the pool.
  - Mark 429 in `llm_providers.rate_limited_until = now + retry_delay` (from response header or default 60s) so the admin status screen shows which providers are currently throttled.
- [ ] **Error registration on every provider switch** — write to `app_metadata`:
  - `llm_provider_switch_last`: `"TIMESTAMP | from: LABEL | reason: 429/503 | to: LABEL"` — overwritten on each switch
  - `llm_provider_switch_count`: increment integer counter
  - `llm_all_exhausted_last`: `"TIMESTAMP | invoice: INVOICE_NUMBER"` — set when all providers fail; **cleared** on the next successful LLM call
- [ ] Unit tests: mock HTTP; 429 on provider 1 → tries provider 2; all fail → `AllProvidersExhausted`; round-robin index advances after success; `app_metadata` written correctly on switch and exhaustion

---

## 6. Store Resolution

Chain identity is determined by LLM from the store's human-readable name, not by PIB. PIB is used only as a fast-path cache key to avoid redundant LLM calls for the same legal entity.

`stores` schema: `id, chain_name, pib` — `chain_name` is the canonical identity (LLM-assigned), `pib` is an optional lookup optimisation.

- [ ] `resolve_store(conn, llm, store_pib, store_name_raw) -> store_id`
  1. PIB cache lookup: `SELECT id FROM stores WHERE pib = ?` → return if found (no LLM call)
  2. Miss → single LLM call: "What retail chain is this store? Raw name: `{store_name_raw}`. Reply with just the canonical chain name (e.g. Lidl, Maxi, DM, Metro)."
  3. Chain name lookup: `SELECT id FROM stores WHERE chain_name = ?` → if found, UPDATE `pib` on that row (handles new PIB for known chain), return id
  4. Both miss → INSERT new `stores(chain_name, pib)` row
- [ ] Unit tests: repeat PIB → no LLM call; new PIB for known chain → updates PIB, no duplicate store; genuinely new chain → new row inserted

---

## 7. Classification Drain

Mirrors `sheet_logging` drain: lifespan-managed `asyncio` task, `DINARY_RECEIPT_DRAIN_INTERVAL_SEC` (default 300), claim/release/poison pattern.

**LLM rate-limit budget.** Each receipt requires exactly one LLM call (batch prompt for all unmatched items). The Gemini 2.5-flash free tier allows 20 RPM. A personal tracker produces at most a few receipts per day — there is no need to process them quickly. The drain must therefore pace itself to stay within the free tier rather than trying to process all pending jobs as fast as possible:

- Default drain interval: **300 seconds** (one receipt processed every 5 minutes at most).
- At that rate, LLM usage stays at 0.2 RPM, leaving 19.8 RPM headroom for other providers or future multi-user scenarios.
- `DINARY_RECEIPT_DRAIN_INTERVAL_SEC` env var allows tuning (lower for paid tiers; do not go below 60 for free tier).
- The drain processes **one job per iteration** (not all pending). This is intentional: it keeps the LLM call rate bounded even if a backlog accumulates after an offline period.

- [ ] Drain loop per job:
  1. Claim `receipt_classification_jobs` row (UPDATE with `claim_token + claimed_at`)
  2. Resolve store (PIB lookup or LLM)
  3. Normalise all `receipt_items.name_raw` → `name_normalized`
  4. Rules lookup per item
  5. Single LLM call for unmatched items (batch prompt with full category list)
  6. Aggregate items by `category_id` → INSERT `expenses` (one per category, `amount` = sum, `confidence_level` = MIN)
  7. Level-1 items: no expense; leave `receipt_items.expense_id = NULL`
  8. UPDATE `receipt_items.category_id`, `confidence_level`, `expense_id`
  9. INSERT `classification_rules` for newly classified items (confidence 2-4)
  10. Trim `llm_call_log` to last 200 rows
  11. DELETE `receipt_classification_jobs` row on success; mark `poisoned` on permanent error
- [ ] Circuit breaker for LLM errors (same backoff pattern as sheet-logging Sheets circuit breaker)
- [ ] Unit tests: full drain cycle with mock LLM; level-1 items skipped for expense creation; rules created correctly; poisoning on parse error

---

## 8. LLM Admin API

- [ ] `GET /api/admin/llm-status` — all providers with usage stats and `rate_limited_until`
- [ ] `GET /api/admin/llm-providers` — list all rows
- [ ] `POST /api/admin/llm-providers` — add provider (seeds from `DINARY_LLM_*` env vars on first boot)
- [ ] `PATCH /api/admin/llm-providers/{id}` — update label / model / api_key / priority / is_enabled
- [ ] `DELETE /api/admin/llm-providers/{id}` — remove (refuse if it is the only enabled provider)
- [ ] `POST /api/admin/llm-providers/{id}/test` — fire a minimal classification call; return `ok` or error detail
- [ ] Env var bootstrap: on `init_db`, if `llm_providers` is empty and `DINARY_LLM_PROVIDER` is set, INSERT one seed row
- [ ] Unit tests for each endpoint

---

## 9. User Correction API

- [ ] `PATCH /api/expenses/{id}/category` — `{category_id: int}`
  1. Update `expenses.category_id` and `confidence_level = 4`
  2. Find `receipt_items` linked to this expense; update their `category_id`
  3. Upsert `classification_rules` for `(store_id, item_name_normalized)`: set `category_id`, `confidence_level = 4`, `source = 'user_correction'`
  4. **Silently** find all other `receipt_items` with same `(store_id, item_name_normalized)` across all receipts; update their `category_id` and `confidence_level = 4`
  5. Recalculate parent expense aggregations for affected items (split/merge as needed)
  6. Return `{corrected_expense_id, batch_updated_count}`
  - No "Fix N similar items?" confirmation modal — batch is always applied silently. The rule key (store chain + normalised name) is the natural deduplication boundary.
- [ ] Unit tests: single correction sets conf=4; batch propagation across receipts; expense split when items move to different category

---

## 10. Review API

Single unified feed that powers the PWA review screen:

- [ ] `GET /api/receipts/review/feed?page=N&page_size=20` — unified paginated list:
  - **Block 1** (doubtful, conf < 4): deduplicated by `(store_id, item_name_normalized)`. One row per unique rule. Sorted by `SUM(total_price) DESC` across all receipts where the rule was applied. Includes: normalised name, store chain name, current category, confidence, total amount at stake, occurrence count.
  - **Block 2** (certain, conf = 4): all expenses from receipts, no deduplication, sorted by `receipt.datetime DESC` (newest first).
  - Response includes `{doubtful_count, items: [...], has_more}`. Items carry `is_doubtful: bool` so the PWA knows where block 1 ends.
- [ ] `GET /api/receipts/review/counts` — `{doubtful_rules: int}` for PWA badge (count of unique rules with conf < 4)
- [ ] Unit tests: feed ordering; deduplication in block 1; correct cutover to block 2

---

## 11. PWA Changes

- [x] Receipt scan sends `POST /api/receipts` (not `POST /api/expenses`)
- [x] Offline IndexedDB queue for receipt URLs (separate queue from manual expenses)

### Next Phase (deferred)

- [ ] Review screen: single infinite-scroll feed from `GET /api/receipts/review/feed`. Doubtful items first (visually highlighted), then all others. Visual separator between the two blocks. Badge on nav icon = `doubtful_rules` count; hidden when zero.
- [ ] Correction UX: tap item → pick category → `PATCH /api/expenses/{id}/category`. No confirmation modal — batch update is silent. Screen refreshes, badge decrements if item was doubtful.
- [ ] LLM status screen in settings: provider list with usage bars, add/edit/test provider, enable/disable

> Backend APIs for review/correction (`GET /api/receipts/review/feed`, `GET /api/receipts/review/counts`, `PATCH /api/expenses/{id}/category`) are already implemented and tested. Frontend integration is the only remaining work.

---

## 12. `inv reclassify-receipts`

Operator tool for re-running classification after a bug fix or rule reset. Does not require a server restart.

```
inv reclassify-receipts                     # all receipts
inv reclassify-receipts --from 2026-05-01   # receipts from date
inv reclassify-receipts --receipt-id 42     # single receipt
```

Steps:
1. Resolve target receipt IDs from flags
2. `DELETE FROM expenses WHERE source='receipt' AND receipt_id IN (...)`
3. `UPDATE receipt_items SET category_id=NULL, confidence_level=NULL, expense_id=NULL WHERE receipt_id IN (...)`
4. `INSERT INTO receipt_classification_jobs (receipt_id, ...) ON CONFLICT DO NOTHING`
5. The drain picks up the new jobs and reclassifies at its normal pace (300s interval)

- [ ] Implement in `tasks/receipt.py`
- [ ] `--clear-rules` flag: also deletes `classification_rules` rows for items in the target receipts (use when fixing a systematic misclassification)
- [ ] Requires `--yes` confirmation when scope > 1 receipt (destructive)
- [ ] Unit tests: correct rows deleted and jobs inserted; idempotent if run twice

---

## 13. `inv healthcheck` Integration

`inv healthcheck` queries `app_metadata` for pipeline error events and fails loudly so the operator sees problems without digging through logs.

### LLM provider failures

```
_healthcheck_receipt_llm(results)
```

Reads:
- `llm_provider_switch_last` — non-empty → **FAIL**: print timestamp + from/reason/to details, exit non-zero
- `llm_all_exhausted_last` — non-empty → **FAIL**: all providers were exhausted; receipt classification is stalled; print invoice number + timestamp, exit non-zero
- `llm_provider_switch_count` — print as info (total switches since last server start)

Both keys are cleared automatically when the next successful LLM call completes with the primary provider. The operator who sees the FAIL can verify the receipt was eventually classified (check `receipt_classification_jobs` for pending rows) and if all is resolved, the next successful call clears the flag.

### Receipt fetch fallback

```
_healthcheck_receipt_fetch(results)
```

Reads:
- `receipt_fetch_fallback_last` — non-empty → **FAIL**: `/specifications` was unavailable for at least one receipt; print timestamp + invoice number + reason, exit non-zero. The receipt was still parsed via journal fallback, but the operator should check whether the endpoint recovered.
- `receipt_fetch_fallback_count` — print as info

Cleared automatically when `/specifications` succeeds again for the next receipt.

### Output format (matches existing healthcheck style)

```
FAIL: LLM provider switched — 2026-05-08T10:12:33Z | from: Groq | reason: 429 | to: OpenRouter GPT-OSS
FAIL: /specifications fallback used — 2026-05-08T09:55:01Z | invoice: LQVN7PP7-LQVN7PP7-87236 | reason: HTTP 503
```

- [ ] Implement `_healthcheck_receipt_llm` in `tasks/server.py`
- [ ] Implement `_healthcheck_receipt_fetch` in `tasks/server.py`
- [ ] Call both from the `healthcheck` task (local + remote paths)
- [ ] Unit tests: populated `app_metadata` keys → non-zero exit; cleared keys → OK

---

## 13. Tests (throughout)

- [ ] `inv pre` passes after every task batch
- [ ] Every new function has a unit test in the same session
- [ ] Integration tests cover full end-to-end: QR URL → `POST /api/receipts` → drain → `GET /api/expenses/review` → `PATCH /api/expenses/{id}/category`
- [ ] Vitest for PWA review screen components and correction modal

---

## Implementation Order

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
