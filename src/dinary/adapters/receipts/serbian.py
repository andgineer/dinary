"""Parse Serbian fiscal receipts via the suf.purs.gov.rs API.

Primary path (3 steps):
1. JSON GET  → store metadata (businessName, taxId, totalAmount, invoiceNumber)
2. HTML GET  → session token (embedded in page JS for the /specifications call)
3. POST /specifications → structured item list with decimal quantities

Fallback path (if /specifications fails or returns empty items once the receipt is old enough):
  Parse the `journal` text field from the JSON response. The journal is always
  present in the official JSON response and has a fixed column-aligned format.
"""

import base64
import binascii
import logging
import math
import re
import struct
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import httpx

from dinary.adapters.receipts.types import (
    ParsedReceipt,
    ParserItemsPendingError,
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
_TOTAL_TOLERANCE = 0.02
_TAX_LABEL_RE = re.compile(r"\s*\(([^()\s])\)$")
JOURNAL_FALLBACK_MIN_AGE = timedelta(minutes=3)


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


def _parse_value_fields(line: str) -> tuple[float, float, float] | None:
    parts = line.split()
    if len(parts) != 3:
        return None
    try:
        unit_price, quantity, total_price = (_rsd(part) for part in parts)
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in (unit_price, quantity, total_price)):
        return None
    return unit_price, quantity, total_price


def _join_name_lines(name_lines: list[str]) -> str:
    # The journal hard-wraps at its width, so a fragment may end or begin mid-word.
    return "".join(name_lines).strip()


def _split_tax_label(name: str) -> tuple[str, str]:
    match = _TAX_LABEL_RE.search(name)
    if match is None:
        return name, ""
    return name[: match.start()], match.group(1)


def _is_name_continuation(name_lines: list[str], line: str, width: int) -> bool:
    return bool(name_lines) and len(name_lines[-1]) >= width and _parse_value_fields(line) is None


def _consume_journal_item_line(
    line_number: int,
    line: str,
    name_lines: list[str],
    items: list[ReceiptItem],
    errors: list[str],
) -> list[str]:
    if not line[0].isspace():
        if name_lines:
            errors.append(f"missing value line for item {_join_name_lines(name_lines)!r}")
        return [line]

    if not name_lines:
        errors.append(f"orphan value line at journal line {line_number}")
        return []

    journal_name = _join_name_lines(name_lines)
    values = _parse_value_fields(line)
    if values is None:
        logger.warning(
            "Journal fallback: skipping malformed value line %r (item: %r)",
            line,
            journal_name,
        )
        errors.append(f"malformed value line for item {journal_name!r}")
        return []

    unit_price, quantity, total_price = values
    name_raw, tax_label = _split_tax_label(journal_name)
    items.append(
        ReceiptItem(
            name_raw=name_raw,
            unit_price=unit_price,
            quantity=quantity,
            total_price=total_price,
            tax_label=tax_label,
        ),
    )
    expected_total = round(unit_price * quantity, 2)
    if abs(expected_total - total_price) > _TOTAL_TOLERANCE:
        errors.append(
            f"item arithmetic mismatch for {journal_name!r}: "
            f"{unit_price:.2f} * {quantity:g} = {expected_total:.2f}, "
            f"journal has {total_price:.2f}",
        )
    return []


def _parse_journal(journal: str) -> tuple[list[ReceiptItem], tuple[str, ...]]:
    """Parse and structurally validate items from the fiscal receipt journal text.

    Each item is a name followed by one indented value line (unit_price  qty  total).
    A name longer than the journal width wraps onto further lines, and the name ends
    with the item's tax label in parentheses.
    """
    lines = journal.replace("\r\n", "\n").splitlines()
    start = _find_item_section_start(lines)
    if start is None:
        return [], ("item section header not found",)

    width = len(lines[start - 1])
    items: list[ReceiptItem] = []
    errors: list[str] = []
    name_lines: list[str] = []
    section_ended = False

    for line_number, line in enumerate(lines[start:], start=start + 1):
        if not line.strip():
            continue
        if line.strip().startswith("---") or line.strip().startswith("Укупан"):
            if name_lines:
                errors.append(f"missing value line for item {_join_name_lines(name_lines)!r}")
                name_lines = []
            section_ended = True
            break
        if _is_name_continuation(name_lines, line, width):
            name_lines.append(line)
            continue
        name_lines = _consume_journal_item_line(
            line_number,
            line,
            name_lines,
            items,
            errors,
        )

    if name_lines:
        errors.append(f"missing value line for item {_join_name_lines(name_lines)!r}")
    if not section_ended:
        errors.append("item section terminator not found")

    return items, tuple(errors)


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
        logger.warning("HTML fetch failed for %s (%s)", url, exc)
        return []

    if not token_match:
        logger.warning("Token not found in HTML for %s", url)
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
        logger.warning("Empty /specifications for %s", url)
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "/specifications failed for %s (%s)",
            url,
            exc,
        )
        return []


def _receipt_age(purchase_datetime: str | None) -> timedelta | None:
    if not purchase_datetime:
        return None
    try:
        issued = datetime.fromisoformat(purchase_datetime)
    except ValueError:
        return None
    if issued.tzinfo is None:
        issued = issued.replace(tzinfo=UTC)
    return datetime.now(UTC) - issued


def _ensure_journal_fallback_allowed(purchase_datetime: str | None, invoice_number: str) -> None:
    age = _receipt_age(purchase_datetime)
    if age is not None and age < JOURNAL_FALLBACK_MIN_AGE:
        raise ParserItemsPendingError(
            f"/specifications has no items yet for {invoice_number} issued "
            f"{age.total_seconds():.0f}s ago; journal fallback waits until "
            f"{JOURNAL_FALLBACK_MIN_AGE.total_seconds():.0f}s",
        )


async def parse_receipt(url: str) -> ParsedReceipt:
    """Fetch a Serbian fiscal receipt and return all items with structured data.

    Tries /specifications first (structured JSON with decimal quantities and
    tax details). Falls back to journal text parsing if /specifications is
    unavailable or returns empty items once the receipt is old enough.

    Raises ParserRequestError on network errors, ParserItemsPendingError while a
    recent receipt has no structured items yet, ParserNotIndexedError if neither
    path yields any items.
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
    journal_validation_errors: tuple[str, ...] = ()
    if not items and journal:
        _ensure_journal_fallback_allowed(purchase_datetime, invoice_number)
        items, journal_validation_errors = _parse_journal(journal)
        used_journal_fallback = True

    if not items:
        raise ParserNotIndexedError(
            f"No items found via /specifications or journal for {url}"
            " — receipt may not be indexed by SUF yet",
        )

    items_total = round(sum(i.total_price for i in items), 2)
    total_ok = abs(items_total - total_amount) <= _TOTAL_TOLERANCE
    if used_journal_fallback:
        if not total_ok:
            journal_validation_errors += (
                f"item total {items_total:.2f} does not match receipt total {total_amount:.2f}",
            )
        validation = (
            "passed"
            if not journal_validation_errors
            else f"failed: {'; '.join(journal_validation_errors)}"
        )
        logger.warning("Using journal fallback for %s; validation %s", url, validation)

    return ParsedReceipt(
        store_name=store_name,
        store_pib=store_pib,
        total_amount=total_amount,
        invoice_number=invoice_number,
        items=items,
        items_total=items_total,
        total_ok=total_ok,
        used_journal_fallback=used_journal_fallback,
        purchase_datetime=purchase_datetime,
        journal_validation_errors=journal_validation_errors,
    )
