import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import allure
import pytest

from dinary.adapters.receipts import dispatch
from dinary.adapters.receipts.types import ParserParseError

_SERBIAN_URL = "https://suf.purs.gov.rs/v/?vl=AAAA"
_MNE_URL = (
    "https://mapr.tax.gov.me/ic/#/verify?iic=X&tin=Y&crtd=2026-07-11T15:51:04+02:00&prc=59.10"
)
_MNE_TEST_URL = "https://efitest.tax.gov.me/ic/#/verify?iic=X&tin=Y&crtd=Z&prc=1.00"


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt dispatch")
class TestReceiptCurrency:
    def test_serbian_is_rsd(self):
        assert dispatch.receipt_currency(_SERBIAN_URL) == "RSD"

    def test_montenegrin_is_eur(self):
        assert dispatch.receipt_currency(_MNE_URL) == "EUR"

    def test_montenegrin_test_host_is_eur(self):
        assert dispatch.receipt_currency(_MNE_TEST_URL) == "EUR"

    def test_unknown_host_defaults_to_rsd(self):
        assert dispatch.receipt_currency("https://example.com/x") == "RSD"


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt dispatch")
class TestParseReceiptDispatch:
    def test_serbian_url_calls_serbian_parser(self):
        with (
            patch.object(
                dispatch.serbian,
                "parse_receipt",
                new=AsyncMock(return_value="serbian"),
            ) as ser,
            patch.object(
                dispatch.montenegrin,
                "parse_receipt",
                new=AsyncMock(return_value="mne"),
            ) as mne,
        ):
            result = asyncio.run(dispatch.parse_receipt(_SERBIAN_URL))
        assert result == "serbian"
        ser.assert_awaited_once_with(_SERBIAN_URL)
        mne.assert_not_awaited()

    def test_montenegrin_url_calls_montenegrin_parser(self):
        with (
            patch.object(
                dispatch.serbian,
                "parse_receipt",
                new=AsyncMock(return_value="serbian"),
            ) as ser,
            patch.object(
                dispatch.montenegrin,
                "parse_receipt",
                new=AsyncMock(return_value="mne"),
            ) as mne,
        ):
            result = asyncio.run(dispatch.parse_receipt(_MNE_URL))
        assert result == "mne"
        mne.assert_awaited_once_with(_MNE_URL)
        ser.assert_not_awaited()

    def test_unknown_url_raises_parse_error(self):
        with pytest.raises(ParserParseError):
            asyncio.run(dispatch.parse_receipt("https://example.com/x"))


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt dispatch")
class TestDecodeQrPayloadDispatch:
    def test_montenegrin_url_decoded(self):
        payload = dispatch.decode_qr_payload(_MNE_URL)
        assert payload is not None
        assert payload.amount == Decimal("59.10")

    def test_serbian_url_delegates_to_serbian(self):
        with patch.object(
            dispatch.serbian,
            "decode_qr_payload",
            return_value="serbian-payload",
        ) as ser:
            result = dispatch.decode_qr_payload(_SERBIAN_URL)
        assert result == "serbian-payload"
        ser.assert_called_once_with(_SERBIAN_URL)

    def test_unknown_url_delegates_to_serbian_and_returns_none(self):
        assert dispatch.decode_qr_payload("https://example.com/x") is None
