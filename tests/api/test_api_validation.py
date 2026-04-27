"""POST ``/api/expenses`` validation surface: 422 paths for unknown /
inactive categories / events / tags, plus the inactive carve-outs that
keep idempotent replays serviceable across a reseed.
"""

from unittest.mock import patch

import allure

from dinary.services import ledger_repo

from _api_helpers import _mock_get_rate, _tmp_db  # noqa: F401  (autouse + helper)


@allure.epic("API")
@allure.feature("Expenses (3D) — validation 422")
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
                "date": "2026-04-15",
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
                "date": "2026-04-15",
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
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 422

    def test_inactive_category_returns_422(self, client):
        """A category that exists but was marked ``is_active=FALSE`` by
        a reseed must be treated as unknown for writes."""
        resp = client.post(
            "/api/expenses",
            json={
                "client_expense_id": "e_inactive",
                "amount": 1.0,
                "currency": "RSD",
                "category_id": 3,
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 422

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
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=bad)
        assert resp.status_code == 422
        assert ledger_repo.lookup_existing_expense("e_leak") is None

        good = {**bad, "category_id": 1}
        resp = client.post("/api/expenses", json=good)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert ledger_repo.lookup_existing_expense("e_leak") is not None


@allure.epic("API")
@allure.feature("Expenses (3D) — reseed / inactive carve-outs")
class TestPostExpenseInactiveCarveout:
    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_reseed_deactivation_allows_idempotent_replay_but_rejects_new_posts(
        self,
        _mock_convert_fn,
        client,
    ):
        """End-to-end of the FK-safe-sync → runtime flow:
        1. Post an expense against an active category so an FK from
           ``expenses`` to ``categories`` is established.
        2. Simulate the reseed dropping that category from the active
           vocabulary (``is_active=FALSE``) — the row can't be deleted
           because the FK still pins it, which is the whole point of
           the FK-safe algorithm in ``seed_config``.
        3. A truly-new POST (different ``client_expense_id``) against
           the retired category must return 422.
        4. An idempotent replay (same ``client_expense_id`` + same
           body) must still return 200 duplicate — an offline PWA
           retry must not be silently lost to an operator's reseed
           that happened after the original POST went over the wire.
        """
        post_body = {
            "client_expense_id": "e_pin_1",
            "amount": 10.0,
            "currency": "RSD",
            "category_id": 1,
            "comment": "",
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = ledger_repo.get_connection()
        try:
            # Simulate the FK-safe reseed path: mark the category inactive
            # rather than deleting (which would violate the FK held by
            # the expense we just inserted).
            con.execute(
                "UPDATE categories SET is_active = FALSE WHERE name = 'еда'",
            )
            (kept,) = con.execute(
                "SELECT COUNT(*) FROM expenses WHERE client_expense_id = 'e_pin_1'",
            ).fetchone()
            assert kept == 1
        finally:
            con.close()

        # Truly-new POST with the retired category → 422 (unchanged
        # contract).
        resp = client.post(
            "/api/expenses",
            json={**post_body, "client_expense_id": "e_pin_2"},
        )
        assert resp.status_code == 422

        # Idempotent replay with the same UUID + same body → 200
        # duplicate. This is the PWA offline-retry guarantee: the
        # original POST established the FK pinning the category on
        # disk, so the server can prove this isn't a fresh use of a
        # retired label.
        replay = client.post("/api/expenses", json=post_body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "duplicate"

        # Replay with the same UUID but a *different* payload → 409,
        # as for any other client_expense_id mismatch. The inactive
        # category does not relax the conflict check.
        mismatch = client.post(
            "/api/expenses",
            json={**post_body, "amount": 999.0},
        )
        assert mismatch.status_code == 409

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_inactive_tag_replay_carveout(
        self,
        _mock_convert_fn,
        client,
    ):
        """The tag validator's replay carve-out mirrors the category one:

        1. Post an expense pinned to an active tag.
        2. Retire the tag (admin PATCH or reseed).
        3. Replaying the same POST must still succeed because the
           stored ``expense_tags`` row proves the tag was live when
           the original request hit the wire.
        4. A truly-new POST using the retired tag must 422.
        """
        post_body = {
            "client_expense_id": "e_tag_pin",
            "amount": 5.0,
            "currency": "RSD",
            "category_id": 1,
            "tag_ids": [1],
            "comment": "",
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 1")
        finally:
            con.close()

        # Truly-new POST against the retired tag -> 422.
        new = client.post(
            "/api/expenses",
            json={**post_body, "client_expense_id": "e_tag_new"},
        )
        assert new.status_code == 422
        assert "Inactive tag_ids" in new.json()["detail"]

        # Replay of the original -> 200 duplicate.
        replay = client.post("/api/expenses", json=post_body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["status"] == "duplicate"

    @patch("dinary.api.expenses.get_rate", side_effect=_mock_get_rate)
    def test_inactive_tag_replay_with_mismatched_body_returns_409(
        self,
        _mock_convert_fn,
        client,
    ):
        """Replay path using an inactive tag but a *different* body
        must return 409, not 422.

        Before the M5 refactor, the tag validator raised a blanket
        422 as soon as any tag in the payload was inactive — even
        for a replay whose stored row was a real conflict (amount /
        date / tag-set differs). That masked the true
        duplicate-vs-conflict decision, which belongs to
        ``insert_expense``'s ON CONFLICT compare path. With the
        validator deferring on replay, the compare runs and surfaces
        the real 409.
        """
        post_body = {
            "client_expense_id": "e_tag_mismatch",
            "amount": 5.0,
            "currency": "RSD",
            "category_id": 1,
            "tag_ids": [1],
            "comment": "",
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=post_body)
        assert resp.status_code == 200, resp.text

        con = ledger_repo.get_connection()
        try:
            con.execute("UPDATE tags SET is_active = FALSE WHERE id = 1")
        finally:
            con.close()

        # Same client_expense_id, inactive tag, but *different amount*:
        # this is a genuine conflict, so the compare path should surface
        # 409 — the inactive-tag validator must not hide it behind 422.
        resp = client.post(
            "/api/expenses",
            json={**post_body, "amount": 99.0},
        )
        assert resp.status_code == 409
