# Receipt Pipeline — Implementation Plan

> Design: see "Classification Layer" and "Receipt Scanning" sections in [`architecture.md`](./architecture.md).

## Scope

Phase 2: automatic receipt classification via QR scan, background LLM classification with provider failover, expense aggregation by category, and PWA review / correction UX.

---

## 1. DB Migration

- [ ] Create `src/dinary/migrations/0002_receipt_pipeline.sql`
  - New tables: `stores`, `receipts`, `receipt_items`, `classification_rules`, `receipt_classification_jobs`, `llm_providers`, `llm_call_log`
  - Alter `expenses`: add `receipt_id`, `store_id`, `confidence_level`
- [ ] Unit tests: migration applies cleanly on a fresh DB and on an existing Phase-1 DB

---

## 2. Receipt Ingestion — `POST /api/receipts`

- [ ] Integrate `sr-invoice-parser` (pip dependency): fetch `suf.purs.gov.rs` HTML, parse items (name, quantity, total_price), extract PIB / store_name_raw / store_address / total_amount
- [ ] `POST /api/receipts` endpoint:
  - Idempotent via `client_receipt_id` (`UNIQUE` constraint + payload compare, same pattern as `POST /api/expenses`)
  - Single transaction: INSERT `receipts` + `receipt_items` + `receipt_classification_jobs`
  - Returns `200 OK` immediately (no classification on hot path)
- [ ] Offline queue support in PWA IndexedDB (same pattern as manual expenses)
- [ ] Unit tests: parse real SUF PURS HTML fixture; idempotency replay; offline queue flush

---

## 3. Item Name Normalisation

- [ ] `normalize_item_name(raw: str) -> str`: lowercase, strip trailing weight/volume/pack tokens (regex: `\d+\s*(g|kg|ml|l|kom|pc)\b`), collapse whitespace
- [ ] Unit tests covering Serbian receipt edge cases (abbreviated units, Cyrillic/Latin mix)

---

## 4. Classification Rules Engine

- [ ] `classify_by_rules(conn, store_id, item_name_normalized) -> (category_id, confidence_level) | None`
  - SQL: `WHERE (store_id = ? OR store_id IS NULL) AND item_name_normalized = ? ORDER BY store_id NULLS LAST LIMIT 1`
- [ ] `create_or_update_rule(conn, store_id, item_name_normalized, category_id, confidence_level, source)`
- [ ] Unit tests: store-specific rule beats generic; miss returns None

---

## 5. LLM Provider Abstraction

- [ ] `LLMClient` protocol: `classify_receipt(items: list[str], store_name_raw: str) -> list[ClassificationResult]`
  - `ClassificationResult`: `item_name_normalized`, `category_id | None`, `confidence_level: int`
- [ ] `OpenAICompatibleClient` (covers DeepSeek, Groq, OpenRouter, Gemini via `/v1/chat/completions`)
  - `base_url` + `api_key` + `model` from `llm_providers` row
  - Structured JSON response via system prompt; parse with fallback to `confidence_level=1` on parse error
- [ ] Rate limit tracking in `app_metadata`:
  - Keys: `llm_rl_{id}_until`, `llm_req_{id}_today`, `llm_req_{id}_date`
  - Increment after each call; reset `_today` when `_date` changes
- [ ] Priority failover loop: skip providers where `rl_until > now`; mark `rl_until` on 429; surface `AllProvidersExhausted` when all skip or error
- [ ] Unit tests: mock HTTP; 429 triggers failover to next provider; daily limit exhaustion; all providers exhausted → pending job kept

---

## 6. Store Resolution

- [ ] `resolve_store(conn, llm, store_pib, store_name_raw) -> store_id`
  - PIB lookup first (`SELECT id FROM stores WHERE pib = ?`)
  - Miss → single LLM call: "What chain is this store? Raw name: {store_name_raw}. Reply with just the chain name (e.g. Lidl, Maxi, DM)."
  - INSERT `stores(pib, name)` on miss
- [ ] Unit tests: repeat PIB → no LLM call; new PIB → LLM called once then cached

---

## 7. Classification Drain

Mirrors `sheet_logging` drain: lifespan-managed `asyncio` task, `DINARY_RECEIPT_DRAIN_INTERVAL_SEC` (default 120), claim/release/poison pattern.

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
  1. Update `expenses.category_id`
  2. Find `receipt_items` linked to this expense; update their `category_id`
  3. Update `classification_rules` (or INSERT if no rule exists): `source = 'user_correction'`
  4. Find all other `receipt_items` classified by the same rule (`store_id + item_name_normalized`)
  5. Recalculate their parent expense aggregations (split/merge as needed)
  6. Return `{corrected_expense_id, batch_updated_count}`
- [ ] Unit tests: single correction; batch propagation; expense split when items move to different category

---

## 10. Review API

- [ ] `GET /api/receipts/unresolved` — `receipt_items` where `confidence_level = 1` or `expense_id IS NULL` (not manual entries), with `receipt` context
- [ ] `GET /api/expenses/review?level=2` — expenses with `confidence_level = 2`, newest first
- [ ] `GET /api/expenses/review?level=3` — expenses with `confidence_level = 3`
- [ ] `GET /api/receipts/review/counts` — `{unresolved, uncertain, review}` for PWA badge

---

## 11. PWA Changes

- [ ] Receipt scan sends `POST /api/receipts` (not `POST /api/expenses`)
- [ ] Offline IndexedDB queue for receipt URLs (separate queue from manual expenses)
- [ ] Review screen: three tabs / sections by confidence level with counts
- [ ] Unresolved items: category picker → `PATCH /api/expenses` or manual expense creation
- [ ] Correction UX: tap expense → pick category → "Fix N similar items too?" modal → `PATCH /api/expenses/{id}/category`
- [ ] LLM status screen in settings: provider list with usage bars, add/edit/test provider, enable/disable

---

## 12. Tests (throughout)

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
| 6. LLM admin API | PWA settings |
| 7. Correction API | PWA correction UX |
| 8. Review API | PWA review screen |
| 9. PWA | — |
