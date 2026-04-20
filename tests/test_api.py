"""API tests for the 3D model: GET /api/categories, POST /api/expenses."""

from unittest.mock import patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_duckdb(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")
    duckdb_repo.init_config_db()

    con = duckdb_repo.get_config_connection(read_only=False)
    try:
        con.execute("INSERT INTO category_groups VALUES (1, 'Food', 1)")
        con.execute("INSERT INTO category_groups VALUES (2, 'Transport', 2)")
        con.execute("INSERT INTO categories VALUES (1, 'еда', 1)")
        con.execute("INSERT INTO categories VALUES (2, 'транспорт', 2)")
    finally:
        con.close()


def _mock_convert_to_eur(config_con, amount_original, currency_original, rate_date):
    return amount_original


@allure.epic("API")
@allure.feature("Health")
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@allure.epic("API")
@allure.feature("Categories (3D)")
class TestCategories:
    def test_returns_catalog_version_and_categories(self, client):
        resp = client.get("/api/categories")
        assert resp.status_code == 200
        data = resp.json()
        assert data["catalog_version"] == 1
        names = {c["name"] for c in data["categories"]}
        assert {"еда", "транспорт"}.issubset(names)
        for cat in data["categories"]:
            assert cat["group"] in {"Food", "Transport"}

    def test_db_failure_returns_500(self, client, monkeypatch):
        def bad_connection(**_kwargs):
            raise RuntimeError("DB corrupted")

        monkeypatch.setattr(duckdb_repo, "get_config_connection", bad_connection)
        resp = client.get("/api/categories")
        # 500 (not 502): config.duckdb is in-process, so a read failure is
        # an internal server error, not an upstream/gateway failure.
        assert resp.status_code == 500


@allure.epic("API")
@allure.feature("Expenses (3D)")
class TestPostExpense:
    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_create_expense(self, mock_schedule, _mock_convert, client):
        resp = client.post(
            "/api/expenses",
            json={
                "expense_id": "e1",
                "amount": 50.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "created"
        assert data["expense_id"] == "e1"
        assert data["catalog_version"] == 1
        mock_schedule.assert_called_once_with("e1", 2026)

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    def test_disabled_sheet_logging_does_not_enqueue_jobs(self, _mock_convert, client, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")

        resp = client.post(
            "/api/expenses",
            json={
                "expense_id": "e_no_log",
                "amount": 50.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "lunch",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text

        con = duckdb_repo.get_budget_connection(2026)
        try:
            assert duckdb_repo.list_logging_jobs(con) == []
        finally:
            con.close()

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_replay_returns_duplicate(self, mock_schedule, _mock_convert, client):
        body = {
            "expense_id": "e2",
            "amount": 50.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        first = client.post("/api/expenses", json=body)
        assert first.status_code == 200
        second = client.post("/api/expenses", json=body)
        assert second.status_code == 200
        assert second.json()["status"] == "duplicate"
        # schedule_logging called once on first insert; idempotent replay does not re-queue.
        assert mock_schedule.call_count == 1

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_conflict_on_modified_amount(self, _sched, _conv, client):
        base = {
            "expense_id": "e3",
            "amount": 50.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        client.post("/api/expenses", json=base)
        modified = {**base, "amount": 99.0}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_cross_year_reuse_rejected(self, _sched, _conv, client):
        client.post(
            "/api/expenses",
            json={
                "expense_id": "shared",
                "amount": 1.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "",
                "date": "2026-01-15",
            },
        )
        resp = client.post(
            "/api/expenses",
            json={
                "expense_id": "shared",
                "amount": 1.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "",
                "date": "2027-01-15",
            },
        )
        assert resp.status_code == 409

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_cross_year_reuse_caught_by_post_reserve_check(
        self,
        _sched,
        _conv,
        client,
    ):
        """Bug K1 regression: the read-only `get_registered_expense_year`
        probe at the top of the handler is racy by design — between that
        probe and `reserve_expense_id_year`, a peer POST against a
        different year can win the registry insert. The handler MUST
        compare `stored_year` returned from `reserve_expense_id_year` to
        the request's `year` and 409 when they differ; without that
        check the POST silently writes into the wrong yearly budget DB
        and the registry diverges from the actual row location.

        We force the probe to return None to simulate the lost race
        (the probe ran *before* the peer's reserve completed), then
        pre-seed the registry to a different year so the reserve call
        returns `(other_year, False)`. The handler must 409 on that
        return value alone."""
        with patch.object(
            duckdb_repo,
            "get_registered_expense_year",
            return_value=None,
        ):
            seed_con = duckdb_repo.get_config_connection(read_only=False)
            try:
                seed_con.execute(
                    "INSERT INTO expense_id_registry (expense_id, year) VALUES ('e_race', 2025)",
                )
            finally:
                seed_con.close()

            resp = client.post(
                "/api/expenses",
                json={
                    "expense_id": "e_race",
                    "amount": 1.0,
                    "currency": "RSD",
                    "category": "еда",
                    "comment": "",
                    "date": "2026-04-15",
                },
            )

        assert resp.status_code == 409, resp.text
        assert "2025" in resp.json()["detail"]
        assert duckdb_repo.get_registered_expense_year("e_race") == 2025
        bcon = duckdb_repo.get_budget_connection(2026)
        try:
            row = bcon.execute(
                "SELECT 1 FROM expenses WHERE id = 'e_race'",
            ).fetchone()
        finally:
            bcon.close()
        assert row is None, (
            "Bug K1: budget_2026 must NOT have an `e_race` row — the "
            "post-reserve check is supposed to short-circuit before "
            "`insert_expense` runs."
        )

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    def test_unknown_category_returns_422(self, _conv, client):
        resp = client.post(
            "/api/expenses",
            json={
                "expense_id": "e4",
                "amount": 1.0,
                "currency": "RSD",
                "category": "missing-category",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 422

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    def test_unknown_category_does_not_reserve_registry(self, _conv, client):
        # The unknown-category 422 path bails out before
        # `reserve_expense_id_year` is ever called, so the registry must
        # be empty and a corrected retry must succeed cleanly.
        bad = {
            "expense_id": "e_leak",
            "amount": 1.0,
            "currency": "RSD",
            "category": "missing-category",
            "comment": "",
            "date": "2026-04-15",
        }
        resp = client.post("/api/expenses", json=bad)
        assert resp.status_code == 422
        assert duckdb_repo.get_registered_expense_year("e_leak") is None

        good = {**bad, "category": "еда"}
        resp = client.post("/api/expenses", json=good)
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"
        assert duckdb_repo.get_registered_expense_year("e_leak") == 2026

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_insert_failure_releases_registry_reservation(
        self,
        _sched,
        _conv,
        client,
    ):
        # Bug regression for the actual release-on-failure path: when
        # `insert_expense` raises AFTER `reserve_expense_id_year` has
        # claimed the id, the API must release the registry row so a
        # corrected retry isn't blocked by a phantom reservation.
        # Triggered here by patching `insert_expense` to raise — covers
        # the `release_registry = True; raise` branch in api/expenses.py
        # that the unknown-category test does not exercise (that one
        # bails out before the reservation happens at all).
        #
        # The default TestClient re-raises server exceptions instead of
        # turning them into 500s, so we capture the simulated failure with
        # `pytest.raises` and then assert on the registry state.
        def boom(*_args, **_kwargs):
            raise RuntimeError("simulated DB blip")

        with patch.object(duckdb_repo, "insert_expense", side_effect=boom):
            with pytest.raises(RuntimeError, match="simulated DB blip"):
                client.post(
                    "/api/expenses",
                    json={
                        "expense_id": "e_blip",
                        "amount": 1.0,
                        "currency": "RSD",
                        "category": "еда",
                        "comment": "",
                        "date": "2026-04-15",
                    },
                )
        assert duckdb_repo.get_registered_expense_year("e_blip") is None

        # Corrected retry (real insert_expense back in place) must succeed.
        resp = client.post(
            "/api/expenses",
            json={
                "expense_id": "e_blip",
                "amount": 1.0,
                "currency": "RSD",
                "category": "еда",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        assert duckdb_repo.get_registered_expense_year("e_blip") == 2026

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_conflict_after_registry_wipe_keeps_cross_year_protection(
        self,
        _sched,
        _conv,
        client,
    ):
        """Bug regression: after `inv import-catalog` wipes config.duckdb
        (registry included) but leaves budget DBs intact, a conflicting POST
        used to release the freshly-inserted registry row, opening a
        cross-year reuse hole.

        Sequence: insert e_keep into 2026 → wipe registry only → POST a
        modified e_keep for 2026 (returns 409) → confirm registry now
        points at 2026 → POST e_keep for 2027 (must be rejected as
        cross-year reuse)."""
        base = {
            "expense_id": "e_keep",
            "amount": 1.0,
            "currency": "RSD",
            "category": "еда",
            "comment": "",
            "date": "2026-04-15",
        }
        first = client.post("/api/expenses", json=base)
        assert first.status_code == 200

        # Simulate the import-catalog wipe (registry only; budget DB stays).
        wipe_con = duckdb_repo.get_config_connection(read_only=False)
        try:
            wipe_con.execute("DELETE FROM expense_id_registry")
        finally:
            wipe_con.close()
        assert duckdb_repo.get_registered_expense_year("e_keep") is None

        modified = {**base, "amount": 99.0}
        resp = client.post("/api/expenses", json=modified)
        assert resp.status_code == 409

        # The registry row must survive the conflict so cross-year reuse
        # stays blocked.
        assert duckdb_repo.get_registered_expense_year("e_keep") == 2026
        cross_year = {**base, "date": "2027-04-15"}
        resp = client.post("/api/expenses", json=cross_year)
        assert resp.status_code == 409

    @patch("dinary.api.expenses.convert_to_eur", side_effect=_mock_convert_to_eur)
    @patch("dinary.api.expenses.schedule_logging")
    def test_response_echoes_original_amount_and_currency(
        self,
        _sched,
        _conv,
        client,
    ):
        # Bug regression: response used to expose `amount_rsd` even when the
        # caller submitted EUR. Replaced with `amount_original`/
        # `currency_original` echo.
        resp = client.post(
            "/api/expenses",
            json={
                "expense_id": "e_eur",
                "amount": 12.5,
                "currency": "EUR",
                "category": "еда",
                "comment": "",
                "date": "2026-04-15",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["amount_original"] == 12.5
        assert data["currency_original"] == "EUR"
        assert "amount_rsd" not in data
