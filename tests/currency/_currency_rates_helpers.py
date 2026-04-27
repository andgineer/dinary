"""Shared autouse fixture, ``nbs_mocks`` patch bundle, and date /
rate constants for the split ``test_currency_rates_*.py`` files.

Underscore prefix keeps pytest from collecting this as a test
module. The autouse cache-clear fixture stays scoped to the
currency-rates suite (re-imported per split file) rather than
promoted to ``conftest.py`` so the per-test TTL-cache wipe doesn't
fire for unrelated suites.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from dinary.services.exchange_rates import _fetch_frankfurter_rate
from dinary.services.nbs import _fetch_nbs_rate

# 2025-02-24 Mon, 25 Tue, 28 Fri, Mar 1 Sat, Mar 2 Sun
_MON = date(2025, 2, 24)
_TUE = date(2025, 2, 25)
_FRI = date(2025, 2, 28)
_SAT = date(2025, 3, 1)
_SUN = date(2025, 3, 2)
# 2025-01-01 Wed — Serbian New Year holiday
_HOLIDAY = date(2025, 1, 1)
_DAY_BEFORE_HOLIDAY = date(2024, 12, 31)

_SOURCE = "EUR"
_TARGET = "RSD"
_RATE = Decimal("117.32")
_CON = MagicMock(name="con")


@pytest.fixture(autouse=True)
def _clear_ttl_caches():
    """Clear in-memory TTL caches between tests to avoid cross-test pollution."""
    _fetch_nbs_rate.cache.clear()
    _fetch_frankfurter_rate.cache.clear()
    yield
    _fetch_nbs_rate.cache.clear()
    _fetch_frankfurter_rate.cache.clear()


@pytest.fixture
def nbs_mocks():
    with (
        patch("dinary.services.nbs._get_db_rate") as get_db,
        patch("dinary.services.nbs._save_db_rate") as save_db,
        patch("dinary.services.nbs._fetch_nbs_rate") as fetch_nbs,
    ):
        get_db.return_value = None
        fetch_nbs.return_value = None
        yield get_db, save_db, fetch_nbs


__all__ = [
    "_CON",
    "_DAY_BEFORE_HOLIDAY",
    "_FRI",
    "_HOLIDAY",
    "_MON",
    "_RATE",
    "_SAT",
    "_SOURCE",
    "_SUN",
    "_TARGET",
    "_TUE",
    "_clear_ttl_caches",
    "nbs_mocks",
]
