"""Parse Serbian fiscal receipt QR codes."""

from dataclasses import dataclass
from datetime import date, datetime

import httpx


@dataclass
class ReceiptData:
    amount: float
    date: date


def parse_receipt_url(url: str) -> ReceiptData:
    """Fetch a SUF PURS receipt URL and extract total amount + date."""
    try:
        resp = httpx.get(url, headers={"Accept": "application/json"}, timeout=30.0)
        resp.raise_for_status()
    except httpx.RequestError as exc:
        raise ValueError(f"Request failed: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ValueError(f"HTTP error: {exc}") from exc

    data = resp.json()
    res = (data.get("invoiceResult") or {}) if isinstance(data, dict) else {}

    total = res.get("totalAmount")
    sdc_time = res.get("sdcTime")

    if total is None:
        raise ValueError(f"Could not extract total amount from {url}")
    if sdc_time is None:
        raise ValueError(f"Could not extract date from {url}")

    dt = datetime.fromisoformat(str(sdc_time).replace("Z", "+00:00"))
    return ReceiptData(amount=float(total), date=dt.date())
