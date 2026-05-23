from datetime import date
from unittest.mock import MagicMock, patch

import allure
import httpx
import pytest

from dinary.api.controllers.qr_parser import parse_receipt_url

_JSON_RESPONSE = {
    "invoiceRequest": {"businessName": "LIDL", "taxId": "123"},
    "invoiceResult": {
        "totalAmount": 2500.0,
        "invoiceNumber": "TEST-TEST-001",
        "sdcTime": "2026-04-10T12:00:00.000Z",
    },
}


def _mock_httpx_get(json_body):
    resp = MagicMock(spec=httpx.Response)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=json_body)
    return resp


@allure.epic("Services")
@allure.feature("QR Parser")
class TestQrParser:
    def test_parse_receipt(self):
        with patch(
            "dinary.api.controllers.qr_parser.httpx.get",
            return_value=_mock_httpx_get(_JSON_RESPONSE),
        ):
            result = parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")
        assert result.amount == 2500.0
        assert result.date == date(2026, 4, 10)

    def test_parse_receipt_no_amount(self):
        body = {**_JSON_RESPONSE, "invoiceResult": {"sdcTime": "2026-04-10T12:00:00.000Z"}}
        with patch(
            "dinary.api.controllers.qr_parser.httpx.get", return_value=_mock_httpx_get(body)
        ):
            with pytest.raises(ValueError, match="total amount"):
                parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")

    def test_parse_receipt_no_date(self):
        body = {**_JSON_RESPONSE, "invoiceResult": {"totalAmount": 2500.0}}
        with patch(
            "dinary.api.controllers.qr_parser.httpx.get", return_value=_mock_httpx_get(body)
        ):
            with pytest.raises(ValueError, match="date"):
                parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")

    def test_network_error_raises(self):
        with patch(
            "dinary.api.controllers.qr_parser.httpx.get",
            side_effect=httpx.RequestError("timeout"),
        ):
            with pytest.raises(ValueError, match="Request failed"):
                parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")

    def test_http_error_raises(self):
        resp = MagicMock(spec=httpx.Response)
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        with patch("dinary.api.controllers.qr_parser.httpx.get", return_value=resp):
            with pytest.raises(ValueError, match="HTTP error"):
                parse_receipt_url("https://suf.purs.gov.rs/v/?vl=test")
