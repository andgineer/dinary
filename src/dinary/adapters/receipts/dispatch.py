"""Dispatch fiscal-receipt operations to the right country parser by URL host.

Serbian receipts (`suf.purs.gov.rs`) are denominated in RSD; Montenegrin ones
(`mapr.tax.gov.me` / `efitest.tax.gov.me`) in EUR. The background task and the
manual-resolution flow call these helpers instead of a country-specific parser.
"""

from urllib.parse import urlparse

from dinary.adapters.receipts import montenegrin, serbian
from dinary.adapters.receipts.types import ParsedReceipt, ParserParseError, QrPayload

SERBIAN_HOST = "suf.purs.gov.rs"

_RSD = "RSD"
_EUR = "EUR"


def _is_serbian_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == SERBIAN_HOST


def receipt_currency(url: str) -> str:
    """Return the currency a receipt is denominated in, decided by its URL host."""
    if montenegrin.is_montenegrin_url(url):
        return _EUR
    return _RSD


async def parse_receipt(url: str) -> ParsedReceipt:
    """Fetch and parse a fiscal receipt, dispatching to the country parser.

    Raises ParserParseError for a URL from no recognised fiscal system.
    """
    if _is_serbian_url(url):
        return await serbian.parse_receipt(url)
    if montenegrin.is_montenegrin_url(url):
        return await montenegrin.parse_receipt(url)
    raise ParserParseError(f"Unrecognised fiscal receipt URL: {url}")


def decode_qr_payload(url: str) -> QrPayload | None:
    """Decode amount and purchase time from the QR URL without a network call.

    Returns None when the URL is not a recognised fiscal receipt or the payload
    cannot be decoded.
    """
    if montenegrin.is_montenegrin_url(url):
        return montenegrin.decode_qr_payload(url)
    return serbian.decode_qr_payload(url)
