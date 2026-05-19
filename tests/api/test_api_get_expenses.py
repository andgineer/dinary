"""GET /api/expenses — expense list response shape.

Verifies that amount_original and currency_original are present in each
item returned by the paginated expense list endpoint.
"""

from unittest.mock import patch

import allure


from _api_helpers import _mock_get_rate, db  # noqa: F401


def _post_expense(client, *, expense_id="e1", amount=250.0, currency="RSD"):
    with patch("dinary.api.controllers.expenses.get_rate", side_effect=_mock_get_rate):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": expense_id,
                "amount": amount,
                "currency": currency,
                "category_id": 1,
                "date": "2026-04-15",
            },
        )
    assert resp.status_code == 200, resp.text


@allure.epic("API")
@allure.feature("Expenses — GET list")
class TestGetExpensesList:
    def test_returns_amount_original_and_currency(self, client, db):  # noqa: ARG002
        _post_expense(client, amount=250.0, currency="RSD")
        resp = client.get("/api/expenses?page=1&page_size=20")
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1
        item = items[0]
        assert item["amount_original"] == 250.0
        assert item["currency_original"] == "RSD"

    def test_receipt_id_null_for_manual_entry(self, client, db):  # noqa: ARG002
        _post_expense(client)
        resp = client.get("/api/expenses?page=1&page_size=20")
        item = resp.json()["items"][0]
        assert item["receipt_id"] is None

    def test_pagination_has_more(self, client, db):  # noqa: ARG002
        for i in range(3):
            _post_expense(client, expense_id=f"e{i}")
        resp = client.get("/api/expenses?page=1&page_size=2")
        data = resp.json()
        assert data["has_more"] is True
        assert len(data["items"]) == 2
