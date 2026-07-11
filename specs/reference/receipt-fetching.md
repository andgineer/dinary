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
structured endpoint is unavailable, the pipeline falls silently to the journal
parser.

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
classification proceeds. Blocking on a mismatch would silently discard receipts
where the fiscal device or our parser has a minor rounding difference.

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
