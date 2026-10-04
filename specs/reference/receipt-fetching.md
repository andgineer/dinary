# Receipt Fetching

Dinary accepts fiscal receipts from two countries. The country is determined
from the scanned QR payload itself — the user never selects one. Serbian
receipts (`suf.purs.gov.rs`) are denominated in RSD; Montenegrin receipts
(`mapr.tax.gov.me`, with a test host on `efitest.tax.gov.me`) in EUR. Once
fetched, both flow through the same classification, rules, and Sheets pipeline.

Each expense stores its original amount and currency verbatim and the amount
converted to the accounting currency; conversion uses the official rate for the
purchase date (see [currencies.md](currencies.md)).

## Serbia — three-path fetch with fallback

Structured item data is fetched via two paths, with automatic fallback:

1. **Primary**: the `/specifications` endpoint returns structured JSON items with
   float quantities (correctly handles by-weight items). This endpoint is
   undocumented but used by the tax authority's own consumer portal and by the
   independent `receiptrs` library. It is stable in practice; changing it would
   break the official website.

2. **Fallback**: the `journal` field in the official JSON response contains a
   column-aligned text rendering of the full receipt. It is part of the documented
   API and will not disappear if the consumer portal is redesigned. The fallback
   parser extracts items from this text when the primary path fails.

The primary path requires a session token embedded in the receipt's HTML page,
which adds an extra HTTP request. If the token cannot be extracted or the
structured endpoint is unavailable, the pipeline uses the journal parser. Every
such use is logged at warning level so the behaviour remains observable, but the
fallback alone is not a healthcheck failure.

### Waiting for the structured item list

`/specifications` often has no items for a receipt that was issued a minute or
two ago, while the official JSON and its journal are already available. The
structured list is preferred, so the journal fallback is used only once the
receipt is at least 3 minutes old by its fiscal timestamp. Until then an empty
structured response is a transient condition: the receipt stays in the queue
with the reason "Waiting for receipt items from PURS" and is retried every 15
seconds. When the structured list appears it is used and no fallback happens. A
receipt older than 3 minutes when it is first fetched, or one without a fiscal
timestamp, falls back to the journal immediately, because waiting would not make
its structured list appear.

### Journal validation and operational status

The journal is rendered at a fixed width of 40 columns. A longer item name wraps
onto the following lines at an arbitrary character, so a continuation line can
start with a space or split the tax label. The parser joins the wrapped lines
back into one name. Every journal name ends with the item's tax label in
parentheses; the parser separates it into the tax label, so a journal item has
exactly the same name and tax label as the same item from `/specifications`.
This keeps classification rules learned from a journal receipt applicable to
later structured receipts. Checked against 212 production receipts that have
both sources, the journal parser reproduces every structured name, tax label and
total.

The journal parser validates its coverage of the item section while parsing it:

- every item name, wrapped or not, must have one following numeric value line;
- every value line must contain exactly three finite numeric fields: unit price,
  quantity, and item total;
- value lines without an item name and malformed numeric lines are reported;
- each item total must equal unit price multiplied by quantity within `0.02` RSD;
- the item section must have its normal terminator;
- at least one item must be recovered; and
- the sum of recovered item totals must match `invoiceResult.totalAmount` within
  `0.02` RSD.

A journal result that passes these checks is equivalent to a `/specifications`
result for Dinary's accounting and classification inputs. It is classified
without a confidence penalty and does not fail the healthcheck. The cumulative
journal-fallback counter remains available as informational telemetry.

A journal result that recovers at least one item but has structural or
total-validation errors still produces expenses so a receipt is not silently
discarded. If the recovered item total is below the official receipt total, the
recovered items are retained and the difference becomes a correction expense.
If the recovered item total exceeds the official total, all recovered items are
discarded and the entire official total becomes one correction expense. The
correction uses the most frequently used visible category from the last three
months, has confidence level 1, and carries the comment
`"Коррекция в результате ошибки обработки чека"`.

Every correction expense appears in `NEEDS REVIEW` until the user explicitly
chooses or confirms its category. Corrections are excluded from `Confirm all`.
The review row and edit sheet show both the correction amount and the official
receipt total so the discrepancy is visible before confirmation.
Confirming one updates only that expense: it has no receipt item or classification
rule, so the selected category is never learned as a future item/store rule.

Validation details are stored in `app_metadata` in both mismatch cases. If no
item can be recovered, the existing transient retry path remains in effect. A
recent validation failure makes the healthcheck fail; the mere absence of
`/specifications` does not.

### Production timing observation (2026-08-29)

A read-only review covered 167 Serbian production receipts. Twelve had used the
journal fallback, and all twelve recovered item totals exactly matched the
official receipt total. Re-fetching one of those receipts after
`/specifications` became available produced the same normalized item names,
prices, quantities, and totals; only item order differed.

The failed `/specifications` attempts occurred when receipts were 30.6 to 117.3
seconds old, with a median of about 54 seconds. Comparison by fiscal-terminal
prefix did not establish a deterministic readiness delay. For 11 fallback
observations with a later successful observation from the same terminal, the
nearest higher successful receipt age differed by at most 3 seconds in 7 cases,
10 seconds in 10 cases, and 27 seconds in all 11 cases. These are different
receipts, not longitudinal retries of the same receipt. Several terminals also
had successful younger receipts followed by an older fallback; one terminal had
a successful observation at 87.2 seconds and a fallback at 117.3 seconds.

A review of all 235 Serbian production receipts on 2026-10-04 confirms the
pattern. The 23 empty `/specifications` answers came at receipt ages from 30.6
to 117.3 seconds, and none of the 29 receipts first fetched after 120 seconds
got an empty answer. The structured list is therefore often missing 30 seconds
after issue but has never been missing after two minutes. A receipt that fell
back at 62 seconds of age on 2026-10-04 had its `/specifications` items available
when fetched again later. The 3-minute limit covers the longest observed delay
with a one-minute margin, and the 15-second retry keeps the extra wait for a
normal receipt within one retry of the moment its structured list appears. Each
retry is logged with the receipt's age, which supplies the per-receipt timing
that this cross-sectional review lacks.

## Montenegro — single verification call

Montenegro's e-fiscalization system encodes a plain verification URL in the QR
code, carrying the receipt's amount and purchase time directly as parameters.
Those parameters sit after the URL's `#` fragment rather than in its query
string, and the purchase time's timezone-offset `+` is decoded as a space by
standard query parsers and must be restored. The full receipt contents (seller,
line items with quantities and prices, totals) come from a single call to the
portal's verification service. The portal sits behind a bot filter that rejects
non-browser clients, so requests present a browser-like User-Agent.

## Total validation is non-blocking

After parsing, item totals are compared to the receipt's declared total. A
mismatch above a small tolerance sets a flag and logs a warning, but
classification proceeds. For a journal result, the mismatch is also a journal
validation failure reported by the healthcheck. Blocking on a mismatch would
silently discard receipts where the fiscal device or our parser has a minor
rounding difference.

## Server unreliability and not-yet-indexed receipts

Both government fiscal servers can be slow, intermittently unavailable, or return
no data for a receipt fetched moments after purchase because the receipt is not
indexed yet — fetching the same URL again later returns the full receipt. For
Montenegro the tax authority documents a verification window (receipts are
verifiable for roughly 90 days after issuance), so "no data returned" is never
proof of a bad receipt; since receipts are scanned right after purchase this is
not a practical limitation.

All fetch failures, including a not-yet-indexed empty response, are treated as
transient — the job is released for retry rather than poisoned. Only a
genuinely malformed response (invalid JSON, or JSON that doesn't match the
expected shape) justifies poisoning. A URL from no recognised fiscal system is a
permanent error.

## QR payload as amount/date source

The scanned QR URL encodes the purchase amount and timestamp directly —
independent of any fiscal server. This is the only amount/date source available
for a receipt the server has never returned data for, and is what the manual
resolution flow (see
[classification-pipeline.md](classification-pipeline.md#manual-resolution))
relies on. The currency of the decoded amount follows the receipt's country.
