import asyncio
import json
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import httpx
import pytest

from dinary.adapters.montenegrin_receipt_parser import (
    decode_qr_payload,
    is_montenegrin_url,
    parse_receipt,
)
from dinary.adapters.receipt_types import (
    ParserNotIndexedError,
    ParserParseError,
    ParserRequestError,
)

_FIXTURE = json.loads(
    (Path(__file__).resolve().parent.parent / "fixtures" / "montenegro_verify_invoice.json").read_text(),
)

# A real production verify URL (captured 2026-07-11). Note the params sit after
# the `#/verify` fragment and `crtd` carries a literal `+02:00` offset.
_MNE_URL = (
    "https://mapr.tax.gov.me/ic/#/verify?iic=0D7C3EE1EEBAB4A08F4D5003FAE35E7B"
    "&tin=03257746&crtd=2026-07-11T15:51:04+02:00&ord=27585"
    "&bu=sc782yk198&cr=pe967bd413&sw=qo391jt923&prc=59.10"
)


def _make_response(status: int, body, *, text: str | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.raise_for_status = MagicMock()
    if body is None:
        r.text = text if text is not None else ""
        r.json = MagicMock(side_effect=ValueError("no json"))
    else:
        r.json = MagicMock(return_value=body)
        r.text = text if text is not None else json.dumps(body)
    return r


def _mock_client(response: MagicMock):
    client = AsyncMock()
    client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


def _run(url: str, response: MagicMock):
    ctx, client = _mock_client(response)
    with patch(
        "dinary.adapters.montenegrin_receipt_parser.httpx.AsyncClient",
        return_value=ctx,
    ):
        receipt = asyncio.run(parse_receipt(url))
    return receipt, client


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Montenegrin receipt parser")
class TestIsMontenegrinUrl:
    def test_production_host(self):
        assert is_montenegrin_url(_MNE_URL) is True

    def test_test_host(self):
        assert is_montenegrin_url("https://efitest.tax.gov.me/ic/#/verify?prc=1") is True

    def test_serbian_host_is_not_montenegrin(self):
        assert is_montenegrin_url("https://suf.purs.gov.rs/v/?vl=AAAA") is False


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Montenegrin receipt parser")
class TestDecodeQrPayload:
    def test_amount_and_datetime_from_url(self):
        payload = decode_qr_payload(_MNE_URL)
        assert payload is not None
        assert payload.amount == Decimal("59.10")
        # The `+02:00` offset survives the query-parser space mangling.
        assert payload.purchase_datetime.utcoffset().total_seconds() == 2 * 3600
        assert payload.purchase_datetime == datetime.fromisoformat("2026-07-11T15:51:04+02:00")

    def test_plus_sign_mangled_to_space_is_restored(self):
        # A parser that keeps the raw `+` as a space must still decode correctly.
        mangled = _MNE_URL.replace("+02:00", " 02:00")
        payload = decode_qr_payload(mangled)
        assert payload is not None
        assert payload.purchase_datetime == datetime.fromisoformat("2026-07-11T15:51:04+02:00")

    def test_missing_prc_returns_none(self):
        assert decode_qr_payload("https://mapr.tax.gov.me/ic/#/verify?iic=X") is None


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Montenegrin receipt parser")
class TestParseReceipt:
    def test_maps_store_and_total(self):
        receipt, _ = _run(_MNE_URL, _make_response(200, deepcopy(_FIXTURE)))
        assert receipt.store_name == "BLUE MARLIN DOO - HI"
        assert receipt.store_pib == "03257746"
        assert receipt.total_amount == pytest.approx(59.10)
        assert receipt.invoice_number == "sc782yk198/27585/2026/pe967bd413"
        assert receipt.purchase_datetime == "2026-07-11T13:51:04.000+0000"

    def test_maps_all_items_with_vat_labels(self):
        receipt, _ = _run(_MNE_URL, _make_response(200, deepcopy(_FIXTURE)))
        assert len(receipt.items) == 5
        coke = next(i for i in receipt.items if i.name_raw == "COCA COLA ZERO")
        assert coke.unit_price == pytest.approx(3.6)
        assert coke.quantity == pytest.approx(1.0)
        assert coke.total_price == pytest.approx(3.6)
        assert coke.tax_label == "21%"

    def test_decimal_quantity(self):
        data = deepcopy(_FIXTURE)
        data["items"][0]["quantity"] = 0.44
        data["items"][0]["priceAfterVat"] = 1.58
        receipt, _ = _run(_MNE_URL, _make_response(200, data))
        assert receipt.items[0].quantity == pytest.approx(0.44)

    def test_total_ok_true(self):
        receipt, _ = _run(_MNE_URL, _make_response(200, deepcopy(_FIXTURE)))
        assert receipt.total_ok is True

    def test_total_mismatch_flagged_non_blocking(self):
        data = deepcopy(_FIXTURE)
        data["totalPrice"] = 999.99
        receipt, _ = _run(_MNE_URL, _make_response(200, data))
        assert receipt.total_ok is False
        assert len(receipt.items) == 5

    def test_sends_form_fields_from_url(self):
        _, client = _run(_MNE_URL, _make_response(200, deepcopy(_FIXTURE)))
        data = client.post.call_args.kwargs["data"]
        assert data["iic"] == "0D7C3EE1EEBAB4A08F4D5003FAE35E7B"
        assert data["tin"] == "03257746"
        # The offset `+` is preserved in what we send back to the portal.
        assert data["dateTimeCreated"] == "2026-07-11T15:51:04+02:00"

    def test_empty_body_is_not_indexed(self):
        with pytest.raises(ParserNotIndexedError):
            _run(_MNE_URL, _make_response(200, None, text="   "))

    def test_no_items_is_not_indexed(self):
        data = deepcopy(_FIXTURE)
        data["items"] = []
        with pytest.raises(ParserNotIndexedError):
            _run(_MNE_URL, _make_response(200, data))

    def test_malformed_json_is_parse_error(self):
        with pytest.raises(ParserParseError):
            _run(_MNE_URL, _make_response(200, None, text="<html>not json</html>"))

    def test_url_missing_params_is_parse_error(self):
        with pytest.raises(ParserParseError):
            _run("https://mapr.tax.gov.me/ic/#/verify?prc=1", _make_response(200, deepcopy(_FIXTURE)))

    def test_network_error_is_request_error(self):
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.RequestError("timeout"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "dinary.adapters.montenegrin_receipt_parser.httpx.AsyncClient",
            return_value=ctx,
        ):
            with pytest.raises(ParserRequestError):
                asyncio.run(parse_receipt(_MNE_URL))
