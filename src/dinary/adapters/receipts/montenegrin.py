"""Parse Montenegrin fiscal receipts via the tax authority's EFI portal.

Montenegro's e-fiscalization ("elektronska fiskalizacija", EFI) prints a QR code
that encodes a plain verification URL on the InvoiceCheck portal:

    https://mapr.tax.gov.me/ic/#/verify?iic=<32-hex>&tin=<PIB>&crtd=<ISO-8601>&prc=<decimal>...

The query parameters sit **after the `#` fragment**, so they must be parsed from
the fragment rather than the URL query. `prc` (total) and `crtd` (purchase time)
are readable straight from the URL — no network call needed, which is what the
manual-resolution flow relies on. Full receipt contents (seller, line items,
totals) come from a single POST to the portal's `verifyInvoice` JSON API.
"""

import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
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

MONTENEGRIN_HOSTS = ("mapr.tax.gov.me", "efitest.tax.gov.me")
_REQUEST_TIMEOUT = 30.0
# The portal sits behind a bot filter that rejects non-browser clients (see
# specs/reference/receipt-fetching.md), so present a browser-like User-Agent.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def is_montenegrin_url(url: str) -> bool:
    """True when the receipt URL points at Montenegro's EFI verification portal."""
    return (urlparse(url).hostname or "").lower() in MONTENEGRIN_HOSTS


def _query_params(url: str) -> dict[str, str]:
    """Return the receipt URL's parameters as a flat dict of first values.

    The verify URL keeps its parameters after the `#/verify` fragment, so both
    the query and the fragment are scanned. `crtd`'s `+` timezone-offset sign is
    decoded to a space by any query parser; it is restored here (an ISO 8601
    datetime contains no spaces, so the substitution is safe).
    """
    parsed = urlparse(url)
    parts = [parsed.query]
    if "?" in parsed.fragment:
        parts.append(parsed.fragment.split("?", 1)[1])
    raw = "&".join(p for p in parts if p)
    params = {k: v[0] for k, v in parse_qs(raw).items() if v}
    if "crtd" in params:
        params["crtd"] = params["crtd"].replace(" ", "+")
    return params


def decode_qr_payload(url: str) -> QrPayload | None:
    """Decode amount (`prc`) and purchase time (`crtd`) straight from the QR URL.

    No network call — works even when the fiscal service has nothing for this
    receipt yet. Returns None if the parameters are missing or unparseable.
    """
    params = _query_params(url)
    prc = params.get("prc")
    crtd = params.get("crtd")
    if not prc or not crtd:
        return None
    try:
        amount = Decimal(prc)
        purchase_datetime = datetime.fromisoformat(crtd)
    except (InvalidOperation, ValueError):
        return None
    if purchase_datetime.tzinfo is None:
        return None
    return QrPayload(amount=amount, purchase_datetime=purchase_datetime)


def _verify_url(url: str) -> str:
    host = (urlparse(url).hostname or MONTENEGRIN_HOSTS[0]).lower()
    if host not in MONTENEGRIN_HOSTS:
        host = MONTENEGRIN_HOSTS[0]
    return f"https://{host}/ic/api/verifyInvoice"


def _map_items(raw_items: list) -> list[ReceiptItem]:
    items: list[ReceiptItem] = []
    for it in raw_items:
        if not isinstance(it, dict):
            continue
        items.append(
            ReceiptItem(
                name_raw=str(it.get("name") or ""),
                unit_price=float(it.get("unitPriceAfterVat") or 0),
                quantity=float(it.get("quantity") or 0),
                total_price=float(it.get("priceAfterVat") or 0),
                tax_label=_vat_label(it.get("vatRate")),
            ),
        )
    return items


def _vat_label(vat_rate) -> str:
    if vat_rate is None:
        return ""
    # Whole-number VAT rates ("21.0") read better without the trailing zero.
    number = float(vat_rate)
    return f"{number:g}%"


def _parse_verify_response(url: str, data: dict) -> ParsedReceipt:
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ParserNotIndexedError(
            f"verifyInvoice returned no items for {url}"
            " — receipt may not be registered by the tax authority yet",
        )
    items = _map_items(raw_items)

    seller = data.get("seller") if isinstance(data.get("seller"), dict) else {}
    store_name = str(seller.get("name") or "")
    store_pib = str(seller.get("idNum") or data.get("issuerTaxNumber") or "")
    total_amount = float(data.get("totalPrice") or data.get("totalPriceToPay") or 0)
    invoice_number = str(
        data.get("invoiceNumber") or data.get("invoiceOrderNumber") or "",
    )
    purchase_datetime = str(data.get("dateTimeCreated") or "") or None

    items_total = round(sum(i.total_price for i in items), 2)
    return ParsedReceipt(
        store_name=store_name,
        store_pib=store_pib,
        total_amount=total_amount,
        invoice_number=invoice_number,
        items=items,
        items_total=items_total,
        total_ok=abs(items_total - total_amount) <= 0.02,
        purchase_datetime=purchase_datetime,
    )


async def parse_receipt(url: str) -> ParsedReceipt:
    """Fetch a Montenegrin fiscal receipt and return its structured contents.

    Raises ParserRequestError on network/HTTP errors, ParserNotIndexedError when
    the receipt is not yet registered (retry later), and ParserParseError on a
    malformed or unexpected response body.
    """
    params = _query_params(url)
    iic = params.get("iic")
    crtd = params.get("crtd")
    tin = params.get("tin")
    if not iic or not crtd or not tin:
        raise ParserParseError(f"Montenegrin receipt URL missing iic/crtd/tin: {url}")

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            resp = await client.post(
                _verify_url(url),
                headers=_HEADERS,
                data={"iic": iic, "dateTimeCreated": crtd, "tin": tin},
            )
            resp.raise_for_status()
    except httpx.RequestError as exc:
        raise ParserRequestError(f"Request failed: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ParserRequestError(f"Request failed: {exc}") from exc

    # A not-yet-registered receipt answers 200 with an empty body.
    if not resp.text.strip():
        raise ParserNotIndexedError(
            f"verifyInvoice returned an empty body for {url}"
            " — receipt may not be registered by the tax authority yet",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise ParserParseError(f"Invalid JSON from verifyInvoice for {url}") from exc

    if not isinstance(data, dict):
        raise ParserNotIndexedError(
            f"verifyInvoice returned no receipt for {url}"
            " — receipt may not be registered by the tax authority yet",
        )
    return _parse_verify_response(url, data)
