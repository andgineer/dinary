# Receipt stuck-recovery: stop poisoning on SUF delays + manual escape hatch

## Incident summary

Production has `receipt_id=25` permanently `poisoned` (LIDL purchase, 1120.46 RSD,
scanned 2026-06-08 ~46s after `sdcTime`). At parse time `suf.purs.gov.rs` returned
a *valid but empty* response (no store, `totalAmount=0`, empty `journal`) — almost
certainly because SUF had not indexed the receipt yet. `serbian_receipt_parser.py:248`
raises `ParserParseError` ("No items found via /specifications or journal …"), which
`task.py:266-272` treats as **permanent** and poisons immediately, with no retry.
Re-fetching the same URL minutes later returns the full receipt — confirming the
condition was transient, not malformed data.

Two independent fixes, both already agreed with the user:

1. **Stop misclassifying "no items found" as permanent** — route it through the
   existing infinite-retry-with-backoff instead (the user explicitly chose
   *not* to add a retry ceiling: "повторять бесконечно … именно потому что у нас
   уже есть бесконечные повторы у пользователя должна быть возможность
   преобразовать в расход вручную любой висящий чек").
2. **General manual escape hatch** — let the user convert *any* receipt still
   sitting in the processing queue (pending/in_progress/poisoned, regardless of
   cause) into a real expense on the spot, picking the category themselves. This
   guarantees "a receipt never stays unprocessed forever" independent of whatever
   bug or outage caused the stall — exactly the principle stated in the brainstorm.

A key enabler discovered during investigation: **the purchase amount and date are
already encoded in the QR URL itself** (`vl=` base64 param, decoded client-side
today by `parseReceiptUrl` in `webapp/src/composables/receipt.js:11-27`), so the
manual flow never needs SUF or even `receipts.total_amount` — it can always show
and use a reliable amount.

---

## Part 1 — Reclassify "no items found" as transient

### 1a. New exception type

File: `src/dinary/adapters/serbian_receipt_parser.py`

Add right after `ParserParseError` (line 24-25):

```python
class ParserNotIndexedError(Exception):
    """Raised when SUF returns no items and no journal — the receipt is likely
    not indexed yet (transient; resolves once SUF processes it)."""
```

Change the raise at line 247-248 from `ParserParseError` to the new type:

```python
if not items:
    raise ParserNotIndexedError(
        f"No items found via /specifications or journal for {url}"
        " — receipt may not be indexed by SUF yet"
    )
```

`ParserParseError` stays exactly as-is for genuinely malformed responses
(`"Invalid JSON from {url}"` at line 154, `"Unexpected JSON shape from {url}"` at
line 157) — those really are permanent and should keep poisoning immediately.

### 1b. Route through the existing transient-retry path

File: `src/dinary/background/classification/task.py`

- Add `ParserNotIndexedError` to the import at lines 16-21 (alongside
  `ParserParseError`, `ParserRequestError`, `parse_receipt`, `ParsedReceipt`).
- Add `ParserNotIndexedError` to the transient exception tuple at lines 225-231
  (next to `ParserRequestError`, `httpx.HTTPError`, `ConnectionError`,
  `RateMissingError`, `InsufficientCategoriesError`).
- **No change to `_retry_delay`** (lines 64-74) — keep the existing
  no-ceiling backoff (immediate → 3s → 60s → 15min → 1 day, forever). This is
  the user's explicit choice; Part 2 is the safety valve for receipts that never
  self-heal.

### 1c. Test updates

`tests/services/test_receipt_parser.py`:
- `TestParseReceiptFallback::test_raises_when_both_paths_fail` (lines 194-199):
  change `pytest.raises(ParserParseError)` → `pytest.raises(ParserNotIndexedError)`
  and add `ParserNotIndexedError` to the imports (alongside `ParserParseError`,
  `ParserRequestError` near the top of the file).

`tests/tasks/test_receipt_pipeline.py`:
- Add `ParserNotIndexedError` to the parser-exception import block (lines 25-30).
- New test alongside `test_parser_transient_error_releases_for_retry`
  (lines 224-241), e.g. `test_parser_not_indexed_releases_for_retry`: patch
  `parse_receipt` with `side_effect=ParserNotIndexedError("no items yet")`,
  assert `_expense_count == 0` and `_job_status == "pending"` (copy the
  `notify_new_receipt`/`_schedule_wakeup` patches from the existing test).
- New chaos scenario in `_SCENARIOS` (after `parse.permanent_format_error`,
  lines 483-486):
  ```python
  _Chaos(
      "parse.suf_not_indexed_yet",
      [(_PARSE, {"side_effect": ParserNotIndexedError("no items yet")})],
  ),
  ```

Leave `test_empty_items_from_parser_poisons_job` (lines 209-222) and
`test_parser_permanent_error_poisons_job` (lines 243-254) untouched — they cover
different paths: the defensive `_get_items` `RuntimeError` (task.py:507-514,
caught by the generic `except Exception` at line 273-275) and a genuinely
malformed-format `ParserParseError`, both of which must keep poisoning.

---

## Part 2 — Manual "convert receipt to expense" escape hatch

### 2a. Server-side QR payload decoder

Add to `src/dinary/adapters/serbian_receipt_parser.py` (it already owns the
`vl=` URL format). Port of `parseReceiptUrl`
(`webapp/src/composables/receipt.js:11-27`, byte layout confirmed by
`webapp/tests/composable-receipt.test.js:28-42`: bytes 25-32 = amount as
uint64 little-endian in 1/10000 units, bytes 33-40 = epoch milliseconds as
uint64 big-endian):

```python
@dataclass(slots=True, frozen=True)
class QrPayload:
    amount: Decimal
    purchase_datetime: datetime  # tz-aware, UTC


def decode_qr_payload(url: str) -> QrPayload | None:
    """Decode amount and purchase time straight from the vl= QR parameter.

    No network call — works even when SUF has nothing for this receipt yet.
    Returns None if there's no vl= parameter or the payload doesn't decode.
    """
    vl = parse_qs(urlparse(url).query).get("vl", [None])[0]
    if not vl:
        return None
    try:
        raw = base64.b64decode(vl)
        amount_units = struct.unpack_from("<Q", raw, 25)[0]
        epoch_ms = struct.unpack_from(">Q", raw, 33)[0]
    except (binascii.Error, struct.error, ValueError):
        return None
    return QrPayload(
        amount=Decimal(amount_units) / Decimal(10000),
        purchase_datetime=datetime.fromtimestamp(epoch_ms / 1000, tz=UTC),
    )
```

Needs new imports in the parser module: `base64`, `binascii`, `struct`,
`Decimal`, `datetime`/`UTC`, `parse_qs`/`urlparse`.

This becomes the **single canonical source** for the amount used both for
display (queue listing) and for the actual expense creation — the frontend
does not need its own decode for this flow, avoiding any chance of the
displayed amount diverging from the persisted one.

### 2b. New endpoints

File: `src/dinary/api/receipts.py`. New controller module:
`src/dinary/api/controllers/receipt_queue.py`.

**`GET /api/receipts/queue`** — must be registered *before*
`GET /api/receipts/{receipt_id}` (currently at line 46): FastAPI matches routes
in registration order, and `receipt_id: int` would otherwise swallow the literal
path segment `queue` and fail int-coercion with a 422 before this route is ever
tried.

```python
@router.get("/api/receipts/queue")
def receipt_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    con: sqlite3.Connection = Depends(get_db),
) -> dict:
    return list_stuck_receipts(con, page, page_size)
```

Controller `list_stuck_receipts(con, page, page_size) -> dict` in
`receipt_queue.py`:
- Query every receipt with an active job, oldest first:
  ```sql
  SELECT r.id, r.url, r.store_name_raw, r.created_at,
         j.status, j.retry_count, j.last_error
    FROM receipt_classification_jobs j
    JOIN receipts r ON r.id = j.receipt_id
   ORDER BY r.created_at
   LIMIT ? OFFSET ?
  ```
  (mirror `classification_job_counts` for the total count, or `COUNT(*)` over
  the same join for `has_more`)
- For each row, call `decode_qr_payload(r.url)`; expose `amount`/`purchase_datetime`
  (None if decode fails — the UI shows "amount unknown" and the resolve action
  is disabled for that row, see 2d).
- Response shape: `{"items": [...], "has_more": bool}` — matches the
  `build_rules_feed` convention (`rules.py:178-198`). Each item:
  `{receipt_id, status, retry_count, last_error, created_at, store_name_raw,
  amount, currency, purchase_date}`.

**`POST /api/receipts/{receipt_id}/resolve`**

```python
class ResolveReceiptRequest(BaseModel):
    category_id: int
    tag_ids: list[int] = Field(default_factory=list)
    event_id: int | None = None
    comment: str = ""


@router.post("/api/receipts/{receipt_id}/resolve")
def resolve_receipt(
    receipt_id: int,
    body: ResolveReceiptRequest,
    con: sqlite3.Connection = Depends(get_db),
) -> dict:
    return resolve_receipt_manually(receipt_id, body, con)
```

Controller `resolve_receipt_manually(receipt_id, req, con) -> dict`:
1. Look up the receipt and its job in one query (need `url`, `store_id`,
   `purchase_datetime`, job `status`); 404 "Receipt not found" if no receipt,
   409/422 "Receipt already resolved" if no active job row (it was already
   classified or previously resolved — `complete_job` already removed it).
2. `payload = decode_qr_payload(receipt.url)`; if `None`, raise 422 "Cannot
   determine purchase amount from this receipt's URL" — the one truly-last-resort
   case (malformed/non-fiscal URL) where even the QR has nothing usable; nothing
   sensible can be auto-created, so surface it for the user to delete the receipt
   manually instead.
3. Validate refs the same way `_validate_expense_refs` does
   (`db/expenses.py:130-148`, but write the three `SELECT 1 …` checks inline
   here rather than importing a private `_`-prefixed helper): `category_id`
   exists, `event_id` exists if given, every `tag_id` exists. Raise
   `HTTPException(422, …)` with a clear message on failure.
4. Pick the expense datetime: prefer `receipt.purchase_datetime` (set if SUF
   metadata *was* fetched, e.g. parsing failed only at the items step) and fall
   back to `payload.purchase_datetime`; convert to `settings.user_timezone`
   (same as `create_expense_sync`, `controllers/expenses.py:114`).
5. Convert RSD → accounting currency via `get_rate` exactly like
   `_write_single_item` (`persist.py:106-108`) / `create_expense_sync`
   (`controllers/expenses.py:116-125`) — `RECEIPT_CURRENCY` ("RSD") is a fixed
   domain fact for Serbian fiscal receipts; import it from
   `dinary.background.classification.persist` rather than re-stating the
   literal.
6. Resolve auto-attach event tags via `resolve_event_auto_tag_ids` if
   `event_id` is given (`sheet_mapping.py`, same call as `persist.py:26`/
   `controllers/expenses.py:129`), merge with `req.tag_ids` deduped
   (`controllers/expenses.py:127-131` pattern).
7. Inside `transaction(con)`: raw-SQL insert into `expenses` mirroring
   `_write_single_item` (`persist.py:96-118`) but **without** items/rule
   creation — `confidence_level=4`, `rule_id=NULL`, `receipt_id=<id>`,
   `store_id=<receipt.store_id>` (may be `NULL` — fine, FK is nullable),
   `currency_original=RECEIPT_CURRENCY`. Then `enqueue_for_logging(con,
   expense_id)`, attach tags via `expense_tags` insert, and finally
   `complete_job(con, receipt_id)` (deletes the job row — `db/receipts.py:211-215`).
8. After the transaction commits, call `notify_new_work()` if
   `settings.sheet_logging_enabled` (same as `api/expenses.py:31-32`).
9. Response: `{"status": "ok", "expense_id": ..., "amount_original": ...,
   "currency_original": "RSD", "category_id": ...}`.

### 2c. Frontend

`webapp/src/api/receipts.js` — add, following the `apiRequest` pattern already
used in `review.js`:
```js
export function getReceiptQueue({ page = 1, pageSize = 20 } = {}) {
  return apiRequest(`/api/receipts/queue?page=${page}&page_size=${pageSize}`);
}

export function resolveReceipt(receiptId, { categoryId, tagIds = [], eventId = null, comment = "" }) {
  return apiRequest(`/api/receipts/${receiptId}/resolve`, {
    method: "POST",
    body: { category_id: categoryId, tag_ids: tagIds, event_id: eventId, comment },
  });
}
```

UI: a "Stuck receipts" section in `ReviewView.vue`, next to the existing
queue-chips (lines 132-143) — likely worth showing only when
`receiptsQueue.poisoned > 0` or the oldest pending/in_progress item is old, but
the user already decided to **show everything, sorted by age, no threshold** so
the section just lists whatever `getReceiptQueue` returns. Each row shows
`store_name_raw` (or "unknown store"), `amount`/`currency`/`purchase_date`
(or "amount unknown" if `decode` failed — disable the action for that row),
`status`/`retry_count`/age, and a "Save as expense" button. Clicking it opens
a category picker (reuse `CategorySheet.vue`, the same component
`ExpenseEditSheet.vue` uses) and on confirm calls `resolveReceipt`, then
refreshes the queue list and `receiptsQueue` counts.

Add the corresponding store actions (load queue page, resolve-and-refresh) to
`useReviewStore` (`webapp/src/stores/review.js`) following the existing
`loadNextPage`/`correct` patterns (lines 77-? / 108-?).

### 2d. Tests

Backend:
- New `tests/services/test_qr_payload.py` (or extend
  `test_receipt_parser.py`): unit tests for `decode_qr_payload` — valid payload
  (build a 64-byte buffer with `struct.pack_into("<Q", buf, 25, units)` /
  `struct.pack_into(">Q", buf, 33, epoch_ms)`, base64-encode, assert decoded
  `amount`/`purchase_datetime`; mirror the JS fixture in
  `webapp/tests/composable-receipt.test.js:28-50` so both sides agree on the
  byte layout), missing `vl=` → `None`, malformed base64 → `None`, truncated
  buffer → `None`.
- New `tests/api/test_api_receipt_queue.py`: `GET /api/receipts/queue` (empty,
  populated, ordering by `created_at`, pagination); `POST
  /api/receipts/{id}/resolve` happy path (creates exactly one expense with
  `confidence_level=4`, `rule_id=NULL`, correct `amount_original`/`category_id`,
  deletes the job row, `receiptsQueue` counts drop); 404 for unknown receipt;
  409/422 for a receipt with no active job (already resolved); 422 for
  category/event/tag validation failures; 422 for a URL with no decodable `vl=`.
- Extend `tests/tasks/test_receipt_pipeline.py::TestReceiptPipelineNeverLost`
  with a test that posts a receipt, lets it get poisoned (mock
  `ParserParseError`), then calls the resolve endpoint and asserts
  `_assert_not_lost` holds and the job is gone — closing the loop on "no
  receipt is ever permanently stuck".

Frontend: component test for the new "stuck receipts" section (mirroring
existing `RuleRow`/review-component tests), covering the empty state, a row
with a decoded amount, a row with `amount: null` (decode failed → action
disabled), and the resolve-confirm flow calling `resolveReceipt` and refreshing
the list.
