"""Parse Serbian fiscal receipt QR codes via sr-invoice-parser."""

import logging
from dataclasses import dataclass
from datetime import date

from sr_invoice_parser import InvoiceParser

logger = logging.getLogger(__name__)


@dataclass
class ReceiptData:
    amount: float
    date: date


def parse_receipt_url(url: str) -> ReceiptData:
    """Fetch a SUF PURS receipt page and extract total amount + date.

    Uses sr-invoice-parser which handles both JSON and HTML responses
    from the government verification server.
    """
    parser = InvoiceParser(url)
    total = parser.get_total_amount()
    dt = parser.get_dt()

    if total is None:
        raise ValueError(f"Could not extract total amount from {url}")
    if dt is None:
        raise ValueError(f"Could not extract date from {url}")

    return ReceiptData(amount=float(total), date=dt.date())
