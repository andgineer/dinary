"""Tests for the currency picker HTTP surface.

Covers:

* ``GET    /api/currencies``         — saved-list shape
* ``POST   /api/currencies``         — add (idempotent, validation)
* ``DELETE /api/currencies/{code}``  — remove (default protected)

The PWA does not need server-side exchange rates: rate conversion
runs inside ``POST /api/expenses`` at write time. There is therefore
no rate endpoint to test here.
"""

import shutil

import allure
import pytest

from dinary.config import settings
from dinary.services import storage


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", dst)
    # Ensure init_db re-seeds the saved-currency table for the
    # tmp DB. ``client`` runs ``create_app`` which enters the
    # FastAPI lifespan and calls ``storage.init_db``.


@allure.epic("API")
@allure.feature("Currencies — saved list (CRUD)")
class TestCurrenciesCrud:
    def test_seeded_with_default_app_currency(self, client):
        resp = client.get("/api/currencies")
        assert resp.status_code == 200
        body = resp.json()
        # Default app_currency seeded on first init_db.
        assert body["default_code"] == settings.app_currency.upper()
        assert settings.app_currency.upper() in body["codes"]

    def test_post_adds_normalised_code(self, client):
        resp = client.post("/api/currencies", json={"code": "usd"})
        assert resp.status_code == 200
        body = resp.json()
        assert "USD" in body["codes"]

    def test_post_is_idempotent(self, client):
        client.post("/api/currencies", json={"code": "USD"})
        resp = client.post("/api/currencies", json={"code": "USD"})
        assert resp.status_code == 200
        body = resp.json()
        # 'USD' appears exactly once even after a second POST.
        assert body["codes"].count("USD") == 1

    def test_post_rejects_non_iso_code(self, client):
        # Three letters but contains a digit -> validation error.
        resp = client.post("/api/currencies", json={"code": "US1"})
        assert resp.status_code == 422

    def test_post_rejects_wrong_length(self, client):
        # Pydantic min/max length=3 traps short / long codes before
        # they reach our normaliser.
        assert client.post("/api/currencies", json={"code": "US"}).status_code == 422
        assert client.post("/api/currencies", json={"code": "USDX"}).status_code == 422

    def test_delete_removes_existing(self, client):
        client.post("/api/currencies", json={"code": "USD"})
        resp = client.delete("/api/currencies/USD")
        assert resp.status_code == 200
        body = resp.json()
        assert "USD" not in body["codes"]

    def test_delete_is_idempotent_on_missing(self, client):
        # Deleting a code that does not exist is a no-op success;
        # this matches the legacy frontend's "fire and forget"
        # delete from the manage panel and avoids a stale
        # client-vs-server view leaking 404 noise to the operator.
        resp = client.delete("/api/currencies/JPY")
        assert resp.status_code == 200

    def test_delete_default_is_blocked(self, client):
        default = settings.app_currency.upper()
        resp = client.delete(f"/api/currencies/{default}")
        assert resp.status_code == 409
        # The default is still in the list after a failed delete.
        body = client.get("/api/currencies").json()
        assert default in body["codes"]

    def test_delete_rejects_invalid_code(self, client):
        resp = client.delete("/api/currencies/12X")
        assert resp.status_code == 422
