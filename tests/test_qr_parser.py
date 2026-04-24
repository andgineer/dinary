from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import allure
import pytest

from dinary.services.qr_parser import parse_receipt_url


@allure.epic("Services")
@allure.feature("QR Parser")
class TestQrParser:
    @patch("dinary.services.qr_parser.InvoiceParser")
    def test_parse_receipt(self, mock_parser_cls):
        mock_parser = MagicMock()
        mock_parser.get_total_amount.return_value = 2500.00
        mock_parser.get_dt.return_value = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
        mock_parser_cls.return_value = mock_parser

        result = parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")
        assert result.amount == 2500.00
        assert result.date == date(2026, 4, 10)

    @patch("dinary.services.qr_parser.InvoiceParser")
    def test_parse_receipt_no_amount(self, mock_parser_cls):
        mock_parser = MagicMock()
        mock_parser.get_total_amount.return_value = None
        mock_parser.get_dt.return_value = None
        mock_parser_cls.return_value = mock_parser

        with pytest.raises(ValueError, match="total amount"):
            parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")
