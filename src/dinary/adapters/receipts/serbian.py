"""Parse Serbian fiscal receipts via the suf.purs.gov.rs API.

Primary path (3 steps):
1. JSON GET  → store metadata (businessName, taxId, totalAmount, invoiceNumber)
2. HTML GET  → session token (embedded in page JS for the /specifications call)
3. POST /specifications → structured item list with decimal quantities

Fallback path (if /specifications fails or returns empty items):
  Parse the `journal` text field from the JSON response. The journal is always
  present in the official JSON response and has a fixed column-aligned format.
"""

import base64
import binascii
import logging
import re
import struct
from datetime import UTC, datetime
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from dinary.adapters.receipts.types import (
    ParsedReceipt,
    ParserNotIndexedError,
    ParserParseError,
    ParserRequestError,
    QrPayload,
    ReceiptItem,
)

logger = logging.getLogger(__name__)

_SPECS_URL = "https://suf.purs.gov.rs/specifications"
_TOKEN_RE = re.compile(r"viewModel\.Token\('([^']+)'\)")
_REQUEST_TIMEOUT = 30.0


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


# ---------------------------------------------------------------------------
# Journal fallback parser
# ---------------------------------------------------------------------------


def _rsd(s: str) -> float:
    """Parse Serbian decimal format: '1.794,97' → 1794.97, '0,742' → 0.742."""
    return float(s.replace(".", "").replace(",", "."))


def _find_item_section_start(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        s = line.strip()
        if "Укупно" in s and ("Назив" in s or "Naziv" in s or "Цена" in s):
            return i + 1
    return None


def _try_parse_value_line(name: str, line: str) -> ReceiptItem | None:
    parts = line.split()
    if len(parts) < 3:
        return None
    try:
        return ReceiptItem(
            name_raw=name,
            unit_price=_rsd(parts[0]),
            quantity=_rsd(parts[1]),
            total_price=_rsd(parts[2]),
            tax_label="",
        )
    except ValueError:
        logger.warning(
            "Journal fallback: skipping malformed value line %r (item: %r)",
            line,
            name,
        )
        return None


def _parse_journal(journal: str) -> list[ReceiptItem]:
    """Parse items from the fiscal receipt journal text.

    Each item is exactly two lines:
      - Name line: no leading whitespace
      - Value line: leading whitespace  (unit_price  qty  total)
    Correctly handles decimal quantities (KG by-weight items).
    """
    lines = journal.replace("\r\n", "\n").splitlines()
    start = _find_item_section_start(lines)
    if start is None:
        return []

    items: list[ReceiptItem] = []
    current_name: str | None = None

    for line in lines[start:]:
        if not line.strip():
            continue
        if line.strip().startswith("---") or line.strip().startswith("Укупан"):
            break
        if line[0] == " ":
            if current_name is not None:
                item = _try_parse_value_line(current_name, line)
                if item is not None:
                    items.append(item)
            current_name = None
        else:
            current_name = line.strip()

    return items


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


async def _fetch_json_metadata(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str, str, float, str, str, str | None]:
    """Fetch JSON from the receipt URL and return store/invoice metadata.

    Returns (store_name, store_pib, total_amount, invoice_number, journal, purchase_datetime).
    Raises ParserRequestError on network errors, ParserParseError on bad JSON.
    """
    try:
        resp = await client.get(url, headers={"Accept": "application/json"})
        resp.raise_for_status()
    except httpx.RequestError as exc:
        raise ParserRequestError(f"Request failed: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ParserRequestError(f"Request failed: {exc}") from exc

    try:
        data = resp.json()
    except Exception as exc:
        raise ParserParseError(f"Invalid JSON from {url}") from exc

    if not isinstance(data, dict):
        raise ParserParseError(f"Unexpected JSON shape from {url}")

    req = data.get("invoiceRequest") or {}
    res = data.get("invoiceResult") or {}
    purchase_datetime: str | None = str(res.get("sdcTime") or "") or None
    return (
        req.get("businessName") or "",
        req.get("taxId") or "",
        float(res.get("totalAmount") or 0),
        res.get("invoiceNumber") or "",
        data.get("journal") or "",
        purchase_datetime,
    )


async def _fetch_specs_items(
    client: httpx.AsyncClient,
    url: str,
    invoice_number: str,
) -> list[ReceiptItem]:
    """Fetch structured item list from /specifications. Returns [] on any soft failure."""
    try:
        html_resp = await client.get(url)
        html_resp.raise_for_status()
        token_match = _TOKEN_RE.search(html_resp.text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTML fetch failed for %s (%s), falling back to journal", url, exc)
        return []

    if not token_match:
        logger.warning("Token not found in HTML for %s, falling back to journal", url)
        return []

    try:
        specs_resp = await client.post(
            _SPECS_URL,
            data={"invoiceNumber": invoice_number, "token": token_match.group(1)},
        )
        specs_resp.raise_for_status()
        specs = specs_resp.json()
        spec_items = specs.get("items")
        if specs.get("success") and isinstance(spec_items, list) and spec_items:
            return [
                ReceiptItem(
                    name_raw=str(item.get("name") or ""),
                    unit_price=float(item.get("unitPrice") or 0),
                    quantity=float(item.get("quantity") or 0),
                    total_price=float(item.get("total") or 0),
                    tax_label=str(item.get("label") or ""),
                )
                for item in spec_items
            ]
        logger.warning("Empty /specifications for %s, falling back to journal", url)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "/specifications failed for %s (%s), falling back to journal",
            url,
            exc,
        )
        return []


async def parse_receipt(url: str) -> ParsedReceipt:
    """Fetch a Serbian fiscal receipt and return all items with structured data.

    Tries /specifications first (structured JSON with decimal quantities and
    tax details). Falls back to journal text parsing if /specifications is
    unavailable or returns empty items.

    Raises ParserRequestError on network errors, ParserParseError if
    neither path yields any items.
    """
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        (
            store_name,
            store_pib,
            total_amount,
            invoice_number,
            journal,
            purchase_datetime,
        ) = await _fetch_json_metadata(client, url)
        items = await _fetch_specs_items(client, url, invoice_number)

    used_journal_fallback = False
    if not items and journal:
        logger.info("Using journal fallback for %s", url)
        items = _parse_journal(journal)
        used_journal_fallback = True

    if not items:
        raise ParserNotIndexedError(
            f"No items found via /specifications or journal for {url}"
            " — receipt may not be indexed by SUF yet",
        )

    items_total = round(sum(i.total_price for i in items), 2)
    return ParsedReceipt(
        store_name=store_name,
        store_pib=store_pib,
        total_amount=total_amount,
        invoice_number=invoice_number,
        items=items,
        items_total=items_total,
        total_ok=abs(items_total - total_amount) <= 0.02,
        used_journal_fallback=used_journal_fallback,
        purchase_datetime=purchase_datetime,
    )
