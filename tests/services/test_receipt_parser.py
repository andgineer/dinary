import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import httpx
import pytest

from dinary.adapters.receipts.types import (
    ParserNotIndexedError,
    ParserRequestError,
)
from dinary.adapters.receipts.serbian import (
    _parse_journal,
    _rsd,
    parse_receipt,
)

_JOURNAL_WITH_KG = """\
========================================
Назив   Цена         Кол.         Укупно
Grejpfrut/KG/0080040 (Е)
       174,99      2,600          454,97
Mesnata slanina/KG/0227734 (Ђ)
       819,99      0,440          360,80
Karamel čoko/KOM/1002303 (Ђ)
       158,99          1          158,99
----------------------------------------
Укупан износ:                     974,76
"""

_JSON_RESPONSE = {
    "invoiceRequest": {"businessName": "LIDL SRBIJA KD", "taxId": "106884584"},
    "invoiceResult": {
        "totalAmount": 974.76,
        "invoiceNumber": "TEST-TEST-001",
        "sdcTime": "2026-05-01T08:30:00.000Z",
    },
    "journal": _JOURNAL_WITH_KG,
    "isValid": True,
}

_HTML_WITH_TOKEN = "<html><script>viewModel.Token('abc-token-123'); viewModel.InvoiceNumber('TEST-TEST-001');</script></html>"

_SPECS_RESPONSE = {
    "success": True,
    "items": [
        {
            "name": "Grejpfrut/KG/0080040",
            "quantity": 2.6,
            "total": 454.97,
            "unitPrice": 174.99,
            "label": "Е",
        },
        {
            "name": "Mesnata slanina/KG/0227734",
            "quantity": 0.44,
            "total": 360.80,
            "unitPrice": 819.99,
            "label": "Ђ",
        },
        {
            "name": "Karamel čoko/KOM/1002303",
            "quantity": 1.0,
            "total": 158.99,
            "unitPrice": 158.99,
            "label": "Ђ",
        },
    ],
}

_SPECS_EMPTY = {"success": False, "items": []}


def _make_response(status: int, body) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.raise_for_status = MagicMock()
    if isinstance(body, str):
        r.text = body
        r.json = MagicMock(return_value={})
    else:
        r.json = MagicMock(return_value=body)
        r.text = json.dumps(body)
    return r


def _mock_async_client(json_resp, html_resp, specs_resp):
    """Return an async context-manager mock for httpx.AsyncClient."""
    client = AsyncMock()
    client.get = AsyncMock(
        side_effect=[
            _make_response(200, json_resp),
            _make_response(200, html_resp),
        ]
    )
    client.post = AsyncMock(return_value=_make_response(200, specs_resp))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt parser")
class TestParseReceiptPrimary:
    def test_returns_store_info(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert receipt.store_name == "LIDL SRBIJA KD"
        assert receipt.store_pib == "106884584"

    def test_all_items_from_specs(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert len(receipt.items) == 3
        assert receipt.items[0].tax_label == "Е"  # tax_label only from /specifications

    def test_kg_decimal_quantity_from_specs(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        grejpfrut = next(i for i in receipt.items if "Grejpfrut" in i.name_raw)
        assert grejpfrut.quantity == pytest.approx(2.6)
        assert grejpfrut.total_price == pytest.approx(454.97)

    def test_total_ok(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert receipt.total_ok is True

    def test_total_mismatch_flagged(self):
        bad = {
            **_JSON_RESPONSE,
            "invoiceResult": {"totalAmount": 999.99, "invoiceNumber": "TEST-TEST-001"},
        }
        ctx, _ = _mock_async_client(bad, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert receipt.total_ok is False

    def test_token_and_invoice_number_sent(self):
        ctx, client = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        post_call = client.post.call_args
        assert post_call.kwargs["data"]["token"] == "abc-token-123"
        assert post_call.kwargs["data"]["invoiceNumber"] == "TEST-TEST-001"

    def test_purchase_datetime_extracted(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert receipt.purchase_datetime == "2026-05-01T08:30:00.000Z"

    def test_purchase_datetime_none_when_missing(self):
        no_time = {
            **_JSON_RESPONSE,
            "invoiceResult": {"totalAmount": 974.76, "invoiceNumber": "TEST-TEST-001"},
        }
        ctx, _ = _mock_async_client(no_time, _HTML_WITH_TOKEN, _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert receipt.purchase_datetime is None


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt parser")
class TestParseReceiptFallback:
    def test_falls_back_when_specs_empty(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, _HTML_WITH_TOKEN, _SPECS_EMPTY)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert len(receipt.items) == 3

    def test_falls_back_when_token_missing(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, "<html>no token</html>", _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        assert len(receipt.items) == 3
        assert all(i.tax_label == "" for i in receipt.items)  # no tax label in journal

    def test_fallback_kg_decimal_quantity(self):
        ctx, _ = _mock_async_client(_JSON_RESPONSE, "<html>no token</html>", _SPECS_RESPONSE)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            receipt = asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))
        grejpfrut = next(i for i in receipt.items if "Grejpfrut" in i.name_raw)
        assert grejpfrut.quantity == pytest.approx(2.6)
        assert grejpfrut.total_price == pytest.approx(454.97)

    def test_raises_when_both_paths_fail(self):
        no_journal = {**_JSON_RESPONSE, "journal": ""}
        ctx, _ = _mock_async_client(no_journal, "<html>no token</html>", _SPECS_EMPTY)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ParserNotIndexedError):
                asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))

    def test_network_error_raises(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("dinary.adapters.receipts.serbian.httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ParserRequestError):
                asyncio.run(parse_receipt("https://suf.purs.gov.rs/v/?vl=test"))


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt parser")
class TestParseJournal:
    def test_kg_item_decimal_quantity(self):
        items = _parse_journal(_JOURNAL_WITH_KG)
        grejpfrut = next(i for i in items if "Grejpfrut" in i.name_raw)
        assert grejpfrut.quantity == pytest.approx(2.6)
        assert grejpfrut.total_price == pytest.approx(454.97)

    def test_no_items_merged(self):
        items = _parse_journal(_JOURNAL_WITH_KG)
        assert len(items) == 3

    def test_all_items_present(self):
        items = _parse_journal(_JOURNAL_WITH_KG)
        names = [i.name_raw for i in items]
        assert any("Grejpfrut" in n for n in names)
        assert any("Mesnata" in n for n in names)
        assert any("Karamel" in n for n in names)


@allure.epic("Receipts")
@allure.feature("Pipeline")
@allure.story("Receipt parser")
class TestRsd:
    def test_simple_decimal(self):
        assert _rsd("133,55") == pytest.approx(133.55)

    def test_thousands_separator(self):
        assert _rsd("1.794,97") == pytest.approx(1794.97)

    def test_decimal_weight(self):
        assert _rsd("0,742") == pytest.approx(0.742)

    def test_integer(self):
        assert _rsd("1") == pytest.approx(1.0)
