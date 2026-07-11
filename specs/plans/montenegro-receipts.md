# Montenegro fiscal receipts — implementation plan

> **Source of truth:** [issue #23 — Support Montenegro fiscal receipt barcodes](https://github.com/andgineer/dinary/issues/23).
> The functional requirements in the issue win over any detail in this plan. If an
> implementation detail below turns out to be wrong (an endpoint changed, a field is
> named differently), adapt the implementation to satisfy the issue — do not bend the
> functionality to match this plan.

## Research summary

### Montenegro's fiscal system (EFI)

Montenegro runs its own e-fiscalization system ("elektronska fiskalizacija", EFI),
operated by the tax administration. Every fiscal receipt carries a QR code that encodes
a plain **verification URL** on the tax authority's "InvoiceCheck" portal:

```
https://mapr.tax.gov.me/ic/#/verify?iic=<32-hex>&tin=<seller PIB>&crtd=<ISO-8601 with offset>&ord=<n>&bu=<code>&cr=<code>&sw=<code>&prc=<decimal>
```

(The test environment uses host `efitest.tax.gov.me` with the same path and parameters;
format confirmed against a public fiscalization example project.)

Query parameters:

| Param | Meaning |
|---|---|
| `iic` | Invoice Identification Code (IKOF) — 32 hex chars, unique per receipt |
| `tin` | Seller's tax number (PIB) |
| `crtd` | Creation date-time, ISO 8601 **with timezone offset**, e.g. `2021-01-04T12:50:30+01:00` |
| `ord` | Invoice ordinal number |
| `bu` | Business unit code |
| `cr` | Cash register code |
| `sw` | Software code |
| `prc` | Total price as a plain decimal, e.g. `179.79` |

Two properties matter for us:

1. **The QR URL itself carries the total (`prc`) and purchase time (`crtd`)** — the
   Montenegrin analog of Serbia's binary `vl=` payload, but as plain query parameters.
   No network call and no binary decoding is needed to get amount/date, which is what
   the manual-resolution flow needs when the fiscal server never returns data.
2. The verification portal is a SPA; the receipt data behind it is served by a JSON
   API: `POST https://mapr.tax.gov.me/ic/api/verifyInvoice` with form-encoded fields
   `iic`, `dateTimeCreated` (the `crtd` value, offset included), and `tin`. The
   response is a JSON document describing the registered invoice: seller info
   (name, tax number, address), the item list (name, quantity, unit price, line
   total, VAT rate), and receipt totals.

**Caveat — unverified details.** The `verifyInvoice` endpoint and its parameters are
well known in practice (the official portal and third-party accounting tools use it),
but this plan was written without live access to `mapr.tax.gov.me` (blocked from the
research environment), so the **exact response field names are not confirmed here**.
The first implementation step must be to scan a real Montenegrin receipt, call the
endpoint, and capture the actual JSON as a test fixture. Expected shape (to verify):
items as a list of objects with fields along the lines of `name`, `quantity`,
`unitPriceAfterVat`, `priceAfterVat`, `vatRate`; seller as an object with `name` and
an id/TIN field; a total field consistent with the QR's `prc`. Map from whatever the
real payload contains.

**Validation status (attempted live checks, July 2026).** Live API calls were
attempted from the planning environment and are impossible from it: the sandbox
egress policy denies `*.tax.gov.me` outright, and the portal itself answers
**HTTP 403 to non-browser fetch services** (Anthropic's fetcher was rejected on both
`mapr.tax.gov.me` and `efitest.tax.gov.me`), which suggests a WAF / bot filter in
front of the portal. What *is* confirmed, from two independent open-source examples
(`djordjen/ElektronskaFiskalizacijaNodeJS` and `digitalsolutionsmontenegro/fiskalapi`,
each containing a full example verify URL):

- the verify-URL format and the complete parameter set
  (`iic`, `tin`, `crtd`, `ord`, `bu`, `cr`, `sw`, `prc`) — exactly as described above;
- the query parameters sit after the `#/verify` fragment;
- `crtd` is ISO 8601 with offset and `prc` is a plain decimal total.

Unconfirmed and to be established in step 1: the `verifyInvoice` request/response
shape. A ready-to-run probe (fill values from any real receipt QR):

```bash
curl -sS 'https://mapr.tax.gov.me/ic/api/verifyInvoice' \
  -H 'User-Agent: Mozilla/5.0' -H 'Accept: application/json' \
  --data-urlencode 'iic=<iic from QR>' \
  --data-urlencode 'dateTimeCreated=<crtd from QR, offset included>' \
  --data-urlencode 'tin=<tin from QR>'
```

**Caveat — WAF.** Because the portal rejected two different non-browser clients
during research, the server-side fetch may need browser-like headers (at minimum a
realistic `User-Agent`), and it is possible the service filters by geography. Run
the probe above from the machine that will actually host dinary before writing the
adapter; if it is blocked there, that is a blocking finding to report on the issue,
not something to work around silently.

**Caveat — verification window.** The tax administration documents that consumers can
verify receipts within ~90 days of issuance. An old receipt may stop resolving via the
API even though it is genuine. Since we scan receipts right after purchase this is not
a practical problem, but it means "no data returned" is not proof of a bad receipt —
the existing not-indexed-yet retry semantics plus manual resolution cover this.

### Existing packages — build or reuse?

Searched PyPI and GitHub (July 2026). Findings:

- **No alive Python package exists for consumer-side fetching/parsing of Montenegrin
  fiscal receipts.** Nothing on PyPI targets EFI receipt verification.
- What does exist is **merchant-side** fiscalization tooling — libraries for *issuing*
  invoices to the tax authority (signing XML, generating IIC/IKOF), e.g.
  `digitalsolutionsmontenegro/fiskalapi` (an interface to a commercial eFiskal.me
  service) and a NodeJS example project. None of it fetches or parses an existing
  receipt from its QR code, which is what we need.
- For Serbia we faced the same landscape (only `receiptrs` / `sr-invoice-parser` as
  loose references) and implemented our own parser; it is small and stable.

**Decision: implement it ourselves**, mirroring the Serbian adapter. The whole job is
one HTTP POST plus JSON mapping — far smaller than the Serbian parser (no session
token, no journal text fallback, no binary QR payload). A dependency would add risk
(unmaintained, wrong abstraction) for almost no saved code.

## Current state of the codebase (touchpoints)

- `src/dinary/adapters/serbian_receipt_parser.py` — fetches and parses Serbian
  receipts; defines the parsed-receipt/item dataclasses, the transient/permanent/not-
  indexed error taxonomy, and `decode_qr_payload` (amount+datetime from the QR URL,
  no network).
- `src/dinary/background/classification/task.py` — the background job calls the
  parser with the stored receipt URL; its error taxonomy drives release-for-retry vs
  poison.
- `src/dinary/background/classification/persist.py` — hardcodes
  `RECEIPT_CURRENCY = "RSD"`; converts to the accounting currency via the
  exchange-rates adapter, storing original amount + original currency on expenses.
- `src/dinary/api/controllers/receipt_queue.py` — manual resolution: decodes
  amount/date from the QR URL and uses `RECEIPT_CURRENCY` for the created expense.
- `webapp/src/composables/receipt.js` — frontend QR acceptance: recognizes a scan as
  a fiscal receipt by the `suf.purs.gov.rs` host fragment and decodes amount/date
  from the `vl=` payload for offline display.
- The receipts API and DB store the raw QR URL verbatim; nothing else is
  country-specific.

## Implementation plan

Work through the steps in order; each step ends with `uv run inv pre` green and tests
passing (`uv run pytest`, `cd webapp && npm test`).

### Step 1 — capture real data (blocking prerequisite)

Scan a real Montenegrin receipt (or use any publicly shared verify URL), run the
probe `curl` from the "Validation status" section above (POST `verifyInvoice` with
form fields `iic`, `dateTimeCreated`, `tin` taken from the QR URL), and save the raw
JSON response as a pytest fixture. Run it from the host that will run dinary in
production — see the WAF caveat. Confirm/correct the field mapping assumed below. All parser tests are
driven by this fixture — no live network in tests, same as the Serbian parser tests.

### Step 2 — backend Montenegro adapter

New module `src/dinary/adapters/montenegrin_receipt_parser.py`, shaped like the
Serbian one and returning the **same parsed-receipt and item structures** (import
them from a shared module — per repo rules, move them rather than re-export if that
requires relocation; do not duplicate the dataclasses):

- URL detection helper: a receipt URL is Montenegrin when its host is
  `mapr.tax.gov.me` (accept `efitest.tax.gov.me` too). Note the query string sits
  **after the `#` fragment** (`/ic/#/verify?...`), so parse parameters from the
  fragment as well as the query — standard URL parsing puts them in `fragment`, not
  `query`.
- QR payload decoding (no network): extract `prc` → amount and `crtd` → tz-aware
  datetime from the URL parameters. This is the Montenegrin counterpart of the
  Serbian `decode_qr_payload` and must plug into the same manual-resolution flow.
- Fetch: one POST to `verifyInvoice` (30 s timeout, `httpx`, async — same
  conventions as the Serbian adapter). Reuse the existing error taxonomy:
  network/HTTP errors → transient request error; malformed JSON / unexpected shape →
  permanent parse error; a well-formed "not found / not yet registered" response →
  the not-indexed transient error so the job retries (a receipt scanned seconds
  after purchase may not be queryable yet — same phenomenon as Serbia).
- Map items (name, quantity, unit price, line total; VAT label if present) and store
  metadata (seller name, seller tax number, total, invoice number from `ord`/the
  response). Compute the items-total consistency flag exactly like the Serbian
  parser (non-blocking mismatch, small tolerance). Quantities can be decimal
  (by-weight items).
- Amounts are **EUR** — the parser reports prices as numbers; the currency is decided
  by the dispatch layer (step 3).

### Step 3 — dispatch by URL + per-receipt currency

- A small dispatch function (natural home: the adapters layer) picks the parser by
  URL host: `suf.purs.gov.rs` → Serbian, `mapr.tax.gov.me`/`efitest.tax.gov.me` →
  Montenegrin, anything else → permanent parse error. The background task in
  `task.py` calls the dispatcher instead of the Serbian parser directly.
- Replace the `RECEIPT_CURRENCY = "RSD"` constant in `persist.py` with a
  currency-for-receipt decision derived from the same dispatch (RSD for Serbian
  receipts, EUR for Montenegrin). Expenses already store
  `amount_original`/`currency_original`, and the exchange-rates adapter already
  converts arbitrary pairs to the accounting currency (EUR pairs are covered by the
  existing NBS/NBP bridging) — so the change is passing the right currency, not new
  conversion machinery. Update `receipt_queue.py` (manual resolution) the same way:
  QR payload decoding and the expense currency must both follow the receipt's
  country.
- The purchase datetime from `crtd` carries a timezone offset — normalize consistently
  with how the Serbian path stores purchase datetimes (see
  `specs/reference/timestamps.md`).

### Step 4 — frontend acceptance

- `webapp/src/composables/receipt.js`: recognize `mapr.tax.gov.me` (and
  `efitest.tax.gov.me`) as a fiscal receipt host alongside `suf.purs.gov.rs`, and
  decode `{amount, date}` from `prc`/`crtd` for Montenegrin URLs (remember the
  fragment-vs-query parsing issue). Country choice must be automatic from the URL —
  no UI switch (issue requirement).
- The scanner/queue flow needs no structural change: it enqueues the raw URL and the
  backend does the rest. Where the UI shows the QR-derived amount with a currency,
  show EUR for Montenegrin receipts.

### Step 5 — tests

- Backend: fixture-driven parser tests (happy path, decimal quantities, not-yet-
  registered response, malformed JSON, total mismatch), dispatch tests (both hosts +
  unknown host), persist/currency tests (EUR receipt converted to the accounting
  currency; original EUR amount preserved), manual-resolution tests with a
  Montenegrin URL. Every new function gets tests in the same session (repo rule).
- Frontend: host recognition and `prc`/`crtd` decoding tests next to the existing
  `composable-receipt` tests; an end-to-end scan-enqueue test with a Montenegrin URL
  mirroring the existing suf.purs one.
- Regression: the full existing Serbian suites must stay green untouched.

### Step 6 — specs

Update `specs/reference/receipt-fetching.md` (or split per-country) to describe the
Montenegrin fetch path, the QR-URL-as-amount/date source, and the verification-window
caveat — current state only, no implementation details, per the spec rules. Delete
this plan file once the issue is implemented; plans are ephemeral.

## Out of scope (from the issue)

- Other countries' fiscal systems.
- Changes to classification, category templates, or the Sheets layout.
