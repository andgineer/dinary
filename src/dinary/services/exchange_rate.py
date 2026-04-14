"""Fetch NBS middle exchange rate from kurs.resenje.org."""

import logging
from datetime import date
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://kurs.resenje.org/api/v1"


async def fetch_eur_rsd_rate(rate_date: date) -> Decimal:
    """Return the NBS middle rate for EUR/RSD on the given date.

    The API returns ``{"code": "EUR", "date": "...", "exchange_middle": 117.32}``.
    """
    url = f"{BASE_URL}/currencies/eur/rates/{rate_date.isoformat()}"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

    rate = data.get("exchange_middle")
    if rate is None:
        raise ValueError(f"No exchange_middle in response for {rate_date}")
    return Decimal(str(rate))
