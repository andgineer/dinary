# Receipt Fetching — suf.purs.gov.rs

## Three-path fetch with fallback

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

## Total validation is non-blocking

After parsing, item totals are compared to the receipt's declared total. A
mismatch above a small tolerance sets a flag and logs a warning, but
classification proceeds. Blocking on a mismatch would silently discard receipts
where the fiscal device or our parser has a minor rounding difference.

## Server unreliability

The government fiscal server (`suf.purs.gov.rs`) is unreliable: observed
timeouts, intermittent 503s, and slow responses on the same URL that succeeded
moments earlier. A receipt fetched soon after purchase can come back with a
valid but empty response (no items via `/specifications` or the `journal`)
because SUF hasn't indexed it yet — fetching the same URL again later returns
the full receipt.

All fetch failures, including a not-yet-indexed empty response, are treated as
transient — the job is released for retry rather than poisoned. Only a
genuinely malformed response (invalid JSON, or JSON that doesn't match the
expected shape) justifies poisoning.

## QR payload as amount/date source

The receipt's QR URL encodes the purchase amount and timestamp directly (the
`vl=` query parameter), independent of SUF. This is the only amount/date
source available for a receipt SUF has never returned data for, and is what
the manual resolution flow (see
[classification-pipeline.md](classification-pipeline.md#manual-resolution))
relies on.
