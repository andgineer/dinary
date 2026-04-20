from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import allure
import pytest


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

        db_path = tmp_path / "dinary.duckdb"
        db_migrations.migrate_db(db_path)

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

    def test_convert_identity_for_same_currency(self, tmp_path):
        """EUR to EUR conversion should be identity with rate 1.0."""
        from dinary.services.nbs import convert

        import duckdb
        from dinary.services import db_migrations

        db_path = tmp_path / "dinary.duckdb"
        db_migrations.migrate_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            converted, rate = convert(
                con,
                Decimal("100.00"),
                "EUR",
                "EUR",
                date(2026, 4, 1),
            )
            assert converted == Decimal("100.00")
            assert rate == Decimal("1.000000")
        finally:
            con.close()

    @patch("dinary.services.nbs.httpx.get")
    def test_convert_rsd_to_eur(self, mock_get, tmp_path):
        from dinary.services.nbs import convert

        import duckdb
        from dinary.services import db_migrations

        db_path = tmp_path / "dinary.duckdb"
        db_migrations.migrate_db(db_path)

        def mock_responses(*args, **kwargs):
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
            converted, _rate = convert(
                con,
                Decimal("11700"),
                "RSD",
                "EUR",
                date(2026, 4, 1),
            )
            assert converted == Decimal("100.00")
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
