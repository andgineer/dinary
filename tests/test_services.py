from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from dinary.services.category_store import Category, CategoryStore
from dinary.services.exchange_rate import fetch_eur_rsd_rate


@allure.epic("Services")
@allure.feature("Category Store")
class TestCategoryStore:
    def test_load_and_lookup(self):
        store = CategoryStore()
        store.load(
            [
                Category(name="Food", group="Essentials"),
                Category(name="Cinema", group="Entertainment"),
            ]
        )
        assert store.group_for("Food") == "Essentials"
        assert store.group_for("Cinema") == "Entertainment"
        assert store.group_for("Unknown") is None
        assert len(store.categories) == 2

    def test_has_category(self):
        store = CategoryStore()
        store.load(
            [
                Category(name="Food", group="Essentials"),
                Category(name="Food", group="Travel"),
            ]
        )
        assert store.has_category("Food", "Essentials")
        assert store.has_category("Food", "Travel")
        assert not store.has_category("Food", "Other")
        assert not store.has_category("Unknown", "")

    def test_expired_on_creation(self):
        store = CategoryStore()
        assert store.expired

    def test_not_expired_after_load(self):
        store = CategoryStore()
        store.load([Category(name="X", group="Y")])
        assert not store.expired


@allure.epic("Services")
@allure.feature("Exchange Rate")
class TestExchangeRate:
    @pytest.mark.anyio
    @patch("dinary.services.exchange_rate.httpx.AsyncClient")
    async def test_fetch_rate(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "code": "EUR",
            "date": "2026-04-01",
            "exchange_middle": 117.32,
        }
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        rate = await fetch_eur_rsd_rate(date(2026, 4, 1))
        assert rate == Decimal("117.32")

    @pytest.mark.anyio
    @patch("dinary.services.exchange_rate.httpx.AsyncClient")
    async def test_fetch_rate_missing_field(self, mock_client_cls):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"code": "EUR", "date": "2026-04-01"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with pytest.raises(ValueError, match="No exchange_middle"):
            await fetch_eur_rsd_rate(date(2026, 4, 1))


@allure.epic("Services")
@allure.feature("QR Parser")
class TestQrParser:
    @patch("dinary.services.qr_parser.InvoiceParser")
    def test_parse_receipt(self, mock_parser_cls):
        from datetime import datetime, timezone

        from dinary.services.qr_parser import parse_receipt_url

        mock_parser = MagicMock()
        mock_parser.get_total_amount.return_value = 2500.00
        mock_parser.get_dt.return_value = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        mock_parser_cls.return_value = mock_parser

        result = parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")
        assert result.amount == 2500.00
        assert result.date == date(2026, 4, 10)

    @patch("dinary.services.qr_parser.InvoiceParser")
    def test_parse_receipt_no_amount(self, mock_parser_cls):
        from dinary.services.qr_parser import parse_receipt_url

        mock_parser = MagicMock()
        mock_parser.get_total_amount.return_value = None
        mock_parser.get_dt.return_value = None
        mock_parser_cls.return_value = mock_parser

        with pytest.raises(ValueError, match="total amount"):
            parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")
