# Receipt Fetching — suf.purs.gov.rs API

> What we know about fetching structured receipt data from the Serbian fiscal receipt verification portal.

---

## QR Code Format

Serbian fiscal receipts encode a URL of the form:

```
https://suf.purs.gov.rs/v/?vl=<base64-encoded-payload>
```

The `vl` parameter is an opaque binary blob signed by the fiscal device. It uniquely identifies the receipt on the tax authority's servers.

---

## Available Data Sources

### 1. JSON metadata (official, documented)

```
GET https://suf.purs.gov.rs/v/?vl=...
Accept: application/json
```

Returns:

```json
{
  "invoiceRequest": {
    "taxId": "106884584",
    "businessName": "LIDL SRBIJA KD",
    "locationName": "1056725-Prodavnica br. 0104",
    "address": "...",
    "city": "..."
  },
  "invoiceResult": {
    "totalAmount": 2439.6,
    "invoiceNumber": "LQVN7PP7-LQVN7PP7-87236",
    "sdcTime": "2026-05-05T07:03:23.257Z"
  },
  "journal": "<receipt text>",
  "isValid": true
}
```

**`journal`** is a column-aligned text rendering of the full receipt. It is part of the official JSON response and will not disappear if the consumer portal is redesigned. See §3 below for parsing.

### 2. `/specifications` endpoint (undocumented, de-facto standard)

```
POST https://suf.purs.gov.rs/specifications
Content-Type: application/x-www-form-urlencoded

invoiceNumber=LQVN7PP7-LQVN7PP7-87236&token=<session-token>
```

Returns structured item JSON:

```json
{
  "success": true,
  "items": [
    {
      "name": "Grejpfrut/KG/0080040",
      "quantity": 2.6,
      "unitPrice": 174.99,
      "total": 454.97,
      "taxBaseAmount": 413.61,
      "vatAmount": 41.36,
      "label": "Е",
      "labelRate": 10,
      "gtin": ""
    }
  ]
}
```

**`quantity` is a float** — correctly handles by-weight (KG) items with decimal quantities.
**`label`** = VAT rate code (Е = 10%, Ђ = 20%).

**Session token**: embedded in the HTML page as `viewModel.Token('...')`. Must be extracted from a separate HTML GET request to the same URL (no Accept header). The token is session-specific and cannot be derived from the invoice number or the `vl` parameter.

**Status**: not in official TaxCore documentation. Used by the tax authority's own consumer verification portal and independently by the TypeScript library [`receiptrs`](https://github.com/pejovicvuk/receiptrs). Stable in practice — changing it would break the official website.

### 3. `journal` text parsing (fallback)

The `journal` field from the JSON response contains a column-aligned receipt text. Items follow a strict two-line format:

```
Name/UNIT/barcode (VAT_code)
       unit_price      quantity      total
```

Detection rule: **value lines start with whitespace**; name lines do not. This correctly handles:
- KOM items (integer quantity): `       279,99          1          279,99`
- KG items (decimal quantity): `       179,99      0,742          133,55`

Serbian number format: `.` = thousands separator, `,` = decimal point. Convert with `s.replace(".", "").replace(",", ".")`.

**`sr-invoice-parser` bug**: uses `int()` for quantity, which fails on KG items and silently merges multiple items into one. Our `_parse_journal()` uses `float()` and is not affected.

---

## Fetch Strategy in `receipt_parser.parse_receipt(url)`

Three HTTP requests, with automatic fallback:

1. `GET url Accept:application/json` → store metadata + `journal` text (always succeeds if the receipt exists)
2. `GET url` (HTML) → extract session token via regex `viewModel\.Token\('([^']+)'\)`
3. `POST /specifications {invoiceNumber, token}` → structured items **[primary path]**

If step 2 or 3 fails (token not found, endpoint unavailable, empty response), automatically falls back to parsing `journal` from step 1 **[fallback path]**.

Raises `ParserRequestException` on network errors (step 1).
Raises `ParserParseException` if both paths yield zero items.

---

## Total Validation

After parsing items, `items_total = round(sum(item.total_price), 2)` is compared to `totalAmount` from the JSON. Mismatch > 0.02 RSD sets `ParsedReceipt.total_ok = False` and logs a warning. Classification proceeds regardless — the mismatch is surfaced in the drain log and the admin UI.

---

## `suf.purs.gov.rs` Reliability

The government server is **unreliable**: observed timeouts (30s+), occasional 503, and intermittent slow responses on the same URL that succeeded moments before. The drain must treat all fetch failures as transient and retry rather than poisoning the job immediately.

---

## Item Name Formats by Store

| Store | Format | Example | Normaliser rule |
|---|---|---|---|
| Lidl | `Name/KOM/barcode (VAT)` | `Rotkvica, veza/KOM/0082275 (Е)` | `_LIDL_BARCODE` strips `/KOM/barcode (VAT)` |
| Lidl KG | `Name/KG/barcode (VAT)` | `Banane, rinfuz/KG/0080000 (Е)` | same |
| Lidl variant | `Name.CODE/barcode` | `Sladoled.MK4/7005486` | barcode not stripped (no unit prefix) — low priority |
| METRO | `{size}{unit} Name (VAT)` | `1000ML MC SOJA SOS KO (Ђ)` | `_LEADING_UNIT` strips leading size |
| Idea | `NAME {size}{unit} ... (VAT)` | `VODA NEGAZIRANA 6L MOJ D KOM (Ђ)` | size in middle — not stripped, LLM handles correctly |
| Lidl rinfuz | `Name/barcode` (no unit) | `Paradajz grapolo, rinfuz/0082465` | barcode not stripped — low priority |

VAT codes in names (`(Е)`, `(Ђ)`) are stripped by `_VAT_CODE` regex (case-insensitive, covers Cyrillic letters).

---

## Official TaxCore API (not used)

`POST https://<taxcore_api_url>/api/invoices/verifyInvoice` is the officially documented verification endpoint. It requires **certificate-based authentication** (B2B, for POS device manufacturers) and returns only receipt metadata — no item breakdown. Irrelevant for consumer apps.
