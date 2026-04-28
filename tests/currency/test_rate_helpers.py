"""Unit tests for ``dinary.services.rate_helpers`` HTTP helpers.

Covers ``_get_json_with_retry`` (200, 404, error, retry count) and
``_get_json_or_none`` (success passthrough, HTTPError swallowed,
ValueError swallowed).
"""

from unittest.mock import MagicMock, patch

import allure
import httpx
import pytest
from tenacity import RetryError

from dinary.services.rate_helpers import _get_json_or_none, _get_json_with_retry

_URL = "https://kurs.resenje.org/api/v1/currencies/eur/rates/2025-02-24"


@pytest.fixture(autouse=True)
def _instant_retries():
    original = _get_json_with_retry.retry.sleep
    _get_json_with_retry.retry.sleep = lambda _: None
    yield
    _get_json_with_retry.retry.sleep = original


def _resp(status: int, body: dict | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    if body is not None:
        r.json.return_value = body
    r.raise_for_status = MagicMock(
        side_effect=None
        if status < 400
        else httpx.HTTPStatusError("err", request=MagicMock(), response=r)
    )
    return r


@allure.epic("Services")
@allure.feature("Exchange Rate")
@allure.story("_get_json_with_retry — HTTP fetch with retry")
class TestGetJsonWithRetry:
    @patch("dinary.services.rate_helpers.httpx.get")
    def test_returns_json_on_200(self, mock_get):
        mock_get.return_value = _resp(200, {"rate": 117.32})
        assert _get_json_with_retry(_URL) == {"rate": 117.32}

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_returns_none_on_404(self, mock_get):
        mock_get.return_value = _resp(404)
        assert _get_json_with_retry(_URL) is None

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_raises_on_500_after_retries(self, mock_get):
        mock_get.return_value = _resp(500)
        with pytest.raises(RetryError):
            _get_json_with_retry(_URL)
        assert mock_get.call_count == 3

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_retries_on_network_error_then_succeeds(self, mock_get):
        mock_get.side_effect = [
            httpx.ConnectError("timeout"),
            httpx.ConnectError("timeout"),
            _resp(200, {"rate": 1.0}),
        ]
        assert _get_json_with_retry(_URL) == {"rate": 1.0}
        assert mock_get.call_count == 3

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_passes_kwargs_to_httpx(self, mock_get):
        mock_get.return_value = _resp(200, {})
        _get_json_with_retry(_URL, params={"base": "EUR"})
        mock_get.assert_called_once_with(_URL, timeout=10, params={"base": "EUR"})


@allure.epic("Services")
@allure.feature("Exchange Rate")
@allure.story("_get_json_or_none — HTTP errors as None")
class TestGetJsonOrNone:
    @patch("dinary.services.rate_helpers.httpx.get")
    def test_returns_json_on_success(self, mock_get):
        mock_get.return_value = _resp(200, {"exchange_middle": 117.32})
        assert _get_json_or_none(_URL) == {"exchange_middle": 117.32}

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_returns_none_on_persistent_500(self, mock_get):
        mock_get.return_value = _resp(500)
        assert _get_json_or_none(_URL) is None

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_returns_none_on_network_error(self, mock_get):
        mock_get.side_effect = httpx.ConnectError("timeout")
        assert _get_json_or_none(_URL) is None

    @patch("dinary.services.rate_helpers.httpx.get")
    def test_returns_none_on_invalid_json(self, mock_get):
        r = MagicMock(spec=httpx.Response)
        r.status_code = 200
        r.raise_for_status = MagicMock()
        r.json.side_effect = ValueError("not json")
        mock_get.return_value = r
        assert _get_json_or_none(_URL) is None
