from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.services.category_store import Category, CategoryStore


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
@allure.feature("NBS Exchange Rate")
class TestNbsExchangeRate:
    @patch("dinary.services.nbs.httpx.get")
    def test_get_rate_fetches_and_caches(self, mock_get, tmp_path):
        from dinary.services import duckdb_repo
        from dinary.services.nbs import get_rate

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"exchange_middle": 117.32}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        duckdb_repo.ensure_data_dir()
        from dinary.services import db_migrations

        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        import duckdb

        con = duckdb.connect(str(db_path))
        try:
            rate = get_rate(con, date(2026, 4, 1), "EUR")
            assert rate == Decimal("117.32")

            cached = con.execute(
                "SELECT rate FROM exchange_rates WHERE currency = 'EUR' AND date = '2026-04-01'"
            ).fetchone()
            assert cached is not None
        finally:
            con.close()

    def test_convert_to_eur_identity(self, tmp_path):
        """EUR to EUR conversion should be identity."""
        from dinary.services.nbs import convert_to_eur

        import duckdb
        from dinary.services import db_migrations

        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            result = convert_to_eur(con, Decimal("100.00"), "EUR", date(2026, 4, 1))
            assert result == Decimal("100.00")
        finally:
            con.close()

    @patch("dinary.services.nbs.httpx.get")
    def test_convert_rsd_to_eur(self, mock_get, tmp_path):
        from dinary.services.nbs import convert_to_eur

        import duckdb
        from dinary.services import db_migrations

        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        call_count = 0

        def mock_responses(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            url = args[0]
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            if "/rsd/" in url:
                mock_resp.json.return_value = {"exchange_middle": 1.0}
            elif "/eur/" in url:
                mock_resp.json.return_value = {"exchange_middle": 117.0}
            else:
                mock_resp.json.return_value = {"exchange_middle": 117.0}
            return mock_resp

        mock_get.side_effect = mock_responses

        con = duckdb.connect(str(db_path))
        try:
            result = convert_to_eur(con, Decimal("11700"), "RSD", date(2026, 4, 1))
            assert result == Decimal("100.00")
        finally:
            con.close()


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
