"""Shared helpers for exchange rate modules (DB access, HTTP, constants)."""

import logging
from datetime import date
from decimal import Decimal

import httpx
from tenacity import retry, stop_after_attempt, wait_fixed

logger = logging.getLogger(__name__)

_HTTP_NOT_FOUND = 404
_FETCH_RATE_CACHE_TIME = 15 * 60


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def _get_json_with_retry(url: str, **kwargs) -> dict | None:
    """GET with retry. Returns parsed JSON, None on 404, or raises on persistent failure."""
    resp = httpx.get(url, timeout=10, **kwargs)
    if resp.status_code == _HTTP_NOT_FOUND:
        return None
    resp.raise_for_status()
    return resp.json()


def _get_json_or_none(url: str, **kwargs) -> dict | None:
    """_get_json_with_retry with exceptions as None."""
    try:
        return _get_json_with_retry(url, **kwargs)
    except (httpx.HTTPError, ValueError):
        logger.debug("HTTP fetch failed for %s, backing off %ds", url, _FETCH_RATE_CACHE_TIME)
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _get_db_rate(con, rate_date: date, source: str, target: str) -> Decimal | None:
    row = con.execute(
        "SELECT rate FROM exchange_rates"
        " WHERE date = ? AND source_currency = ? AND target_currency = ?",
        [rate_date, source, target],
    ).fetchone()
    return Decimal(str(row[0])) if row else None


def _save_db_rate(con, rate_date: date, source: str, target: str, rate: Decimal) -> None:
    inverse = (Decimal(1) / rate).quantize(Decimal("0.000001"))
    con.executemany(
        "INSERT INTO exchange_rates (date, source_currency, target_currency, rate)"
        " VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
        [
            [rate_date, source, target, rate],
            [rate_date, target, source, inverse],
        ],
    )


def _get_latest_db_rate(con, source: str, target: str) -> Decimal | None:
    """Last known rate regardless of date. Fallback when Frankfurter is down."""
    row = con.execute(
        "SELECT rate FROM exchange_rates"
        " WHERE source_currency = ? AND target_currency = ?"
        " ORDER BY date DESC LIMIT 1",
        [source, target],
    ).fetchone()
    return Decimal(str(row[0])) if row else None
