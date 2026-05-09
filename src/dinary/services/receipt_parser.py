"""Parse Serbian fiscal receipts via the suf.purs.gov.rs API.

Primary path (3 steps):
1. JSON GET  → store metadata (businessName, taxId, totalAmount, invoiceNumber)
2. HTML GET  → session token (embedded in page JS for the /specifications call)
3. POST /specifications → structured item list with decimal quantities

Fallback path (if /specifications fails or returns empty items):
  Parse the `journal` text field from the JSON response. The journal is always
  present in the official JSON response and has a fixed column-aligned format.
  This fallback correctly handles KG (by-weight) items with decimal quantities,
  fixing the sr-invoice-parser bug (int() vs float() for quantity).
"""

import logging
import re
from dataclasses import dataclass

import httpx
from sr_invoice_parser.exceptions import ParserParseException, ParserRequestException

logger = logging.getLogger(__name__)

_SPECS_URL = "https://suf.purs.gov.rs/specifications"
_TOKEN_RE = re.compile(r"viewModel\.Token\('([^']+)'\)")
_REQUEST_TIMEOUT = 30.0


@dataclass(slots=True)
class ReceiptItem:
    name_raw: str
    unit_price: float
    quantity: float
    total_price: float
    tax_label: str


@dataclass(slots=True)
class ParsedReceipt:
    store_name: str
    store_pib: str
    total_amount: float
    invoice_number: str
    items: list[ReceiptItem]
    items_total: float
    total_ok: bool
    used_journal_fallback: bool = False
    purchase_datetime: str | None = None


# ---------------------------------------------------------------------------
# Journal fallback parser
# ---------------------------------------------------------------------------


def _rsd(s: str) -> float:
    """Parse Serbian decimal format: '1.794,97' → 1794.97, '0,742' → 0.742."""
    return float(s.replace(".", "").replace(",", "."))


def _parse_journal(journal: str) -> list[ReceiptItem]:  # noqa: C901
    """Parse items from the fiscal receipt journal text.

    Each item is exactly two lines:
      - Name line: no leading whitespace
      - Value line: leading whitespace  (unit_price  qty  total)
    Correctly handles decimal quantities (KG by-weight items).
    """
    lines = journal.replace("\r\n", "\n").splitlines()

    start = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        has_total = "Укупно" in stripped
        has_header = "Назив" in stripped or "Naziv" in stripped or "Цена" in stripped
        if has_total and has_header:
            start = i + 1
            break
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
            if current_name is None:
                continue
            parts = line.split()
            if len(parts) >= 3:
                try:  # noqa: SIM105
                    items.append(
                        ReceiptItem(
                            name_raw=current_name,
                            unit_price=_rsd(parts[0]),
                            quantity=_rsd(parts[1]),
                            total_price=_rsd(parts[2]),
                            tax_label="",
                        ),
                    )
                except ValueError:
                    logger.warning(
                        "Journal fallback: skipping malformed value line %r (item: %r)",
                        line,
                        current_name,
                    )
            current_name = None
        else:
            current_name = line.strip()

    return items


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_receipt(url: str) -> ParsedReceipt:  # noqa: C901, PLR0915
    """Fetch a Serbian fiscal receipt and return all items with structured data.

    Tries /specifications first (structured JSON with decimal quantities and
    tax details). Falls back to journal text parsing if /specifications is
    unavailable or returns empty items.

    Raises ParserRequestException on network errors, ParserParseException if
    neither path yields any items.
    """
    with httpx.Client(timeout=_REQUEST_TIMEOUT) as client:
        # Step 1: JSON — store metadata + journal (fallback source)
        try:
            json_resp = client.get(url, headers={"Accept": "application/json"})
            json_resp.raise_for_status()
        except httpx.RequestError as exc:
            raise ParserRequestException(f"Request failed: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise ParserRequestException(f"Request failed: {exc}") from exc

        try:
            data = json_resp.json()
        except Exception as exc:
            raise ParserParseException(f"Invalid JSON from {url}") from exc

        if not isinstance(data, dict):
            raise ParserParseException(f"Unexpected JSON shape from {url}")

        req = data.get("invoiceRequest") or {}
        res = data.get("invoiceResult") or {}
        store_name: str = req.get("businessName") or ""
        store_pib: str = req.get("taxId") or ""
        total_amount: float = float(res.get("totalAmount") or 0)
        invoice_number: str = res.get("invoiceNumber") or ""
        journal: str = data.get("journal") or ""
        # sdcDateTime is the fiscal device signing time — the actual purchase datetime.
        purchase_datetime: str | None = str(res.get("sdcDateTime") or "") or None

        # Step 2: HTML — session token for /specifications
        items: list[ReceiptItem] = []
        try:
            html_resp = client.get(url)
            html_resp.raise_for_status()
            token_match = _TOKEN_RE.search(html_resp.text)

            if token_match:
                token = token_match.group(1)

                # Step 3: /specifications — structured items (primary path)
                try:
                    specs_resp = client.post(
                        _SPECS_URL,
                        data={"invoiceNumber": invoice_number, "token": token},
                    )
                    specs_resp.raise_for_status()
                    specs = specs_resp.json()
                    spec_items = specs.get("items")
                    if specs.get("success") and isinstance(spec_items, list) and spec_items:
                        items = [
                            ReceiptItem(
                                name_raw=str(item.get("name") or ""),
                                unit_price=float(item.get("unitPrice") or 0),
                                quantity=float(item.get("quantity") or 0),
                                total_price=float(item.get("total") or 0),
                                tax_label=str(item.get("label") or ""),
                            )
                            for item in spec_items
                        ]
                    else:
                        logger.warning(
                            "Empty /specifications for %s, falling back to journal",
                            url,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "/specifications failed for %s (%s), falling back to journal",
                        url,
                        exc,
                    )
            else:
                logger.warning("Token not found in HTML for %s, falling back to journal", url)

        except Exception as exc:  # noqa: BLE001
            logger.warning("HTML fetch failed for %s (%s), falling back to journal", url, exc)

    # Fallback: journal text parsing
    used_journal_fallback = False
    if not items and journal:
        logger.info("Using journal fallback for %s", url)
        items = _parse_journal(journal)
        used_journal_fallback = True

    if not items:
        raise ParserParseException(f"No items found via /specifications or journal for {url}")

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
