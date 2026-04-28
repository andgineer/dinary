"""POST ``/api/expenses`` conflict (409) detection: replays of the same
``client_expense_id`` with a modified amount / date / category must
surface the compare path's 409 rather than silently 200-duplicate.
"""

from unittest.mock import patch

import allure

from _api_helpers import _mock_get_rate, db  # noqa: F401  (autouse + helper)


@allure.epic("API")
@allure.feature("Expenses (3D) — conflict 409")
class TestPostExpenseConflict:
    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_conflict_on_modified_amount(self, _mock_convert_fn, client):
        base = {
            "client_expense_id": "e3",
            "amount": 50.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "date": "2026-04-15",
        }
        assert client.post("/api/expenses", json=base).status_code == 200

        modified = {**base, "amount": 99.0}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_conflict_on_modified_date(self, _mock_convert_fn, client):
        """Same ``client_expense_id``, different date is a conflict.
        With the single-DB refactor this replaces the old "cross-year
        registry reuse" path."""
        base = {
            "client_expense_id": "shared",
            "amount": 1.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "date": "2026-01-15",
        }
        assert client.post("/api/expenses", json=base).status_code == 200

        modified = {**base, "date": "2027-01-15"}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_conflict_on_modified_category(self, _mock_convert_fn, client):
        """Replaying the same ``client_expense_id`` with a different
        category is a 409 conflict.

        Regression test for the pre-fix bug where ``_compare_payload``
        did not compare ``category_id`` and silently returned 200
        ``duplicate`` for a category-modified replay.
        """
        base = {
            "client_expense_id": "e_cat_change",
            "amount": 50.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "date": "2026-04-15",
        }
        assert client.post("/api/expenses", json=base).status_code == 200

        modified = {**base, "category_id": 2}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409
