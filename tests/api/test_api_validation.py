"""POST ``/api/expenses`` validation surface: 422 paths for unknown /
inactive categories / events / tags, plus the inactive carve-outs that
keep idempotent replays serviceable across a reseed.
"""

from unittest.mock import patch

import allure

from dinary.db import storage
from dinary.db.expenses import lookup_existing_expense

from _api_helpers import _mock_get_rate, db  # noqa: F401  (autouse + helper)


@allure.epic("Expenses")
@allure.feature("API")
@allure.story("Validation")
class TestPostExpenseValidation:
    def test_unknown_category_returns_422(self, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e4",
                "amount": 1.0,
                "currency": "RSD",
                "category_id": 999,
                "comment": "",
                "expense_datetime": "2026-04-15T12:00:00+02:00",
            },
        )
        assert resp.status_code == 422

    def test_unknown_event_id_returns_422(self, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_bad_evt",
                "amount": 1.0,
                "category_id": 1,
                "event_id": 999,
                "expense_datetime": "2026-04-15T12:00:00+02:00",
            },
        )
        assert resp.status_code == 422

    def test_unknown_tag_returns_422(self, client):
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_bad_tag",
                "amount": 1.0,
                "category_id": 1,
                "tag_ids": [999],
                "expense_datetime": "2026-04-15T12:00:00+02:00",
            },
        )
        assert resp.status_code == 422

    def test_inactive_category_is_activated_on_use(self, client):
        """A category that exists but is ``is_active=FALSE`` (and not
        hidden/retired) is reactivated on first use rather than rejected."""
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_inactive",
                "amount": 1.0,
                "currency": "RSD",
                "category_id": 3,
                "comment": "",
                "expense_datetime": "2026-04-15T12:00:00+02:00",
            },
        )
        assert resp.status_code == 200, resp.text

        con = storage.get_connection()
        try:
            (is_active,) = con.execute(
                "SELECT is_active FROM categories WHERE id = 3",
            ).fetchone()
        finally:
            con.close()
        assert bool(is_active) is True

    def test_unknown_category_does_not_insert_row(self, client):
        """The unknown-category 422 path bails out before
        ``insert_expense``, so no ledger row is created and a corrected
        retry succeeds cleanly."""
        bad = {
            "client_expense_id": "e_leak",
            "amount": 1.0,
            "currency": "RSD",
            "category_id": 999,
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        resp = client.post("/api/expenses", json=bad)
        assert resp.status_code == 422
        assert lookup_existing_expense("e_leak") is None

        good = {**bad, "category_id": 1}
        resp = client.post("/api/expenses", json=good)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert lookup_existing_expense("e_leak") is not None


@allure.epic("Expenses")
@allure.feature("API")
@allure.story("Validation")
class TestPostExpenseInactiveCarveout:
    @patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate)
    def test_reseed_retirement_allows_idempotent_replay_but_rejects_new_posts(
        self,
        _mock_convert_fn,
        client,
    ):
        """See ``specs/reference/catalog-api.md`` for the retirement/replay carve-out
        this pins: new POST against a retired category -> 422, idempotent replay
        -> 200 duplicate, mismatched-body replay -> 409."""
        post_body = {
            "client_expense_id": "e_pin_1",
            "amount": 10.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = storage.get_connection()
        try:
            # FK-safe reseed: retire, don't delete (an expense still references it).
            con.execute(
                "UPDATE categories SET is_active = FALSE, is_retired = TRUE WHERE name = 'food'",
            )
            (kept,) = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e_pin_1'",
            ).fetchone()
            assert kept == 1
        finally:
            con.close()

        resp = client.post(
            "/api/expenses",
            json={**post_body, "client_expense_id": "e_pin_2"},
        )
        assert resp.status_code == 422

        replay = client.post("/api/expenses", json=post_body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "duplicate"

        mismatch = client.post(
            "/api/expenses",
            json={**post_body, "amount": 999.0},
        )
        assert mismatch.status_code == 409

    @patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate)
    def test_inactive_tag_replay_carveout(
        self,
        _mock_convert_fn,
        client,
    ):
        """The tag validator's replay carve-out mirrors the category one, see
        ``specs/reference/catalog-api.md``."""
        post_body = {
            "client_expense_id": "e_tag_pin",
            "amount": 5.0,
            "currency": "RSD",
            "category_id": 1,
            "tag_ids": [1],
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = storage.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 1")
        finally:
            con.close()

        new = client.post(
            "/api/expenses",
            json={**post_body, "client_expense_id": "e_tag_new"},
        )
        assert new.status_code == 422
        assert "Inactive tag_ids" in new.json()["detail"]

        replay = client.post("/api/expenses", json=post_body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "duplicate"

    @patch("dinary.adapters.exchange_rates.get_rate", side_effect=_mock_get_rate)
    def test_inactive_tag_replay_with_mismatched_body_returns_409(
        self,
        _mock_convert_fn,
        client,
    ):
        """Replay with an inactive tag but a different body must return 409, not
        422 — the tag validator defers to ``insert_expense``'s ON CONFLICT compare
        path rather than blanket-rejecting on inactive tags."""
        post_body = {
            "client_expense_id": "e_tag_mismatch",
            "amount": 5.0,
            "currency": "RSD",
            "category_id": 1,
            "tag_ids": [1],
            "comment": "",
            "expense_datetime": "2026-04-15T12:00:00+02:00",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = storage.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 1")
        finally:
            con.close()

        resp = client.post(
            "/api/expenses",
            json={**post_body, "amount": 99.0},
        )
        assert resp.status_code == 409
