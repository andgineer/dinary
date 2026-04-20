"""Tests for admin catalog API endpoints.

The admin surface is token-protected; it powers the PWA's "+ Новый"
flows plus the ``reload-map`` admin button. These tests pin:

* token-absent => 503 everywhere (disabled deployment).
* token present but wrong => 403; token missing => 401.
* POST /api/admin/catalog/<kind> returns the full catalog snapshot
  embedded in the response body so the PWA can swap its cache without
  a second round-trip.
"""

from datetime import datetime
from unittest.mock import patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo, runtime_map


ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")
    duckdb_repo.init_db()
    con = duckdb_repo.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, TRUE)",
        )
    finally:
        con.close()


@pytest.fixture
def auth():
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
def enable_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)


@allure.epic("API")
@allure.feature("Admin catalog — auth")
class TestAdminAuth:
    def test_disabled_when_token_empty(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_api_token", "")
        resp = client.post("/api/admin/catalog/tags", json={"name": "x"})
        assert resp.status_code == 503

    def test_401_when_missing_header(self, client, enable_admin):
        resp = client.post("/api/admin/catalog/tags", json={"name": "x"})
        assert resp.status_code == 401

    def test_403_when_token_wrong(self, client, enable_admin):
        resp = client.post(
            "/api/admin/catalog/tags",
            json={"name": "x"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status_code == 403


@allure.epic("API")
@allure.feature("Admin catalog — add")
class TestAdminAdd:
    def test_add_tag_returns_snapshot(self, client, enable_admin, auth):
        resp = client.post("/api/admin/catalog/tags", json={"name": "t1"}, headers=auth)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["new_id"] >= 1
        assert {t["name"] for t in data["tags"]} == {"t1"}
        # Admin responses ship ETag on the HTTP header, not in the body.
        assert "etag" not in data
        assert resp.headers["ETag"].startswith('W/"catalog-v')

    def test_add_group_then_category(self, client, enable_admin, auth):
        g = client.post(
            "/api/admin/catalog/groups",
            json={"name": "Transport"},
            headers=auth,
        )
        assert g.status_code == 200
        gid = g.json()["new_id"]
        c = client.post(
            "/api/admin/catalog/categories",
            json={"name": "metro", "group_id": gid},
            headers=auth,
        )
        assert c.status_code == 200, c.text
        assert any(cat["name"] == "metro" for cat in c.json()["categories"])

    def test_add_event_with_range(self, client, enable_admin, auth):
        resp = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "trip-2026",
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
            },
            headers=auth,
        )
        assert resp.status_code == 200, resp.text
        events = resp.json()["events"]
        assert any(e["name"] == "trip-2026" for e in events)

    def test_add_event_rejects_bad_range(self, client, enable_admin, auth):
        resp = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "bad",
                "date_from": "2026-06-30",
                "date_to": "2026-06-01",
            },
            headers=auth,
        )
        assert resp.status_code == 422


@allure.epic("API")
@allure.feature("Admin catalog — patch")
class TestAdminPatch:
    def test_patch_rename_and_deactivate_in_one_call(
        self,
        client,
        enable_admin,
        auth,
    ):
        # Seed an unused category (no referencing expenses) so the
        # deactivate half of the PATCH is legal.
        create = client.post(
            "/api/admin/catalog/categories",
            json={"name": "orig", "group_id": 1},
            headers=auth,
        )
        assert create.status_code == 200
        cid = create.json()["new_id"]
        v_before = create.json()["catalog_version"]

        patch_resp = client.patch(
            f"/api/admin/catalog/categories/{cid}",
            json={"name": "renamed", "is_active": False},
            headers=auth,
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()
        # Single PATCH -> single version bump.
        assert data["catalog_version"] == v_before + 1
        # Deactivated categories are excluded from GET snapshot.
        assert not any(c["id"] == cid for c in data["categories"])

    def test_patch_inuse_rollback_preserves_name(
        self,
        client,
        enable_admin,
        auth,
    ):
        create = client.post(
            "/api/admin/catalog/categories",
            json={"name": "pinned", "group_id": 1},
            headers=auth,
        )
        cid = create.json()["new_id"]
        # Pin the category with a real expense row so the deactivate
        # leg of the PATCH raises CatalogInUseError (409).
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
                con,
                client_expense_id="pin-cat-e2e",
                expense_datetime=datetime(2026, 4, 20, 10, 0, 0),
                amount=1.0,
                amount_original=1.0,
                currency_original="RSD",
                category_id=cid,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_logging=False,
            )
        finally:
            con.close()

        resp = client.patch(
            f"/api/admin/catalog/categories/{cid}",
            json={"name": "should-not-stick", "is_active": False},
            headers=auth,
        )
        assert resp.status_code == 409

        # Snapshot: original name survived (atomic rollback).
        snap = client.get("/api/catalog").json()
        assert any(c["id"] == cid and c["name"] == "pinned" for c in snap["categories"])


@allure.epic("API")
@allure.feature("Admin catalog — catalog_version end-to-end")
class TestCatalogVersionE2E:
    def test_add_bumps_catalog_version_visible_in_get_catalog(
        self,
        client,
        enable_admin,
        auth,
    ):
        """The whole point of routing admin writes through catalog_writer:
        a successful add bumps ``catalog_version``, and the PWA's next
        GET /api/catalog reflects that new version (so the ETag changes
        and client-side caches revalidate)."""
        v0 = client.get("/api/catalog").json()["catalog_version"]
        client.post(
            "/api/admin/catalog/tags",
            json={"name": "brand-new-tag"},
            headers=auth,
        )
        snap_resp = client.get("/api/catalog")
        snap = snap_resp.json()
        assert snap["catalog_version"] == v0 + 1
        assert any(t["name"] == "brand-new-tag" for t in snap["tags"])
        # Corresponding ETag (on the HTTP header) moved too so
        # If-None-Match re-validation works.
        assert snap_resp.headers["ETag"] == f'W/"catalog-v{v0 + 1}"'


@allure.epic("API")
@allure.feature("Admin catalog — add status")
class TestAddStatus:
    def test_add_returns_created_on_new_name(self, client, enable_admin, auth):
        resp = client.post(
            "/api/admin/catalog/tags",
            json={"name": "fresh"},
            headers=auth,
        )
        assert resp.json()["status"] == "created"

    def test_add_returns_noop_on_existing_active_name(
        self,
        client,
        enable_admin,
        auth,
    ):
        client.post("/api/admin/catalog/tags", json={"name": "t"}, headers=auth)
        resp = client.post(
            "/api/admin/catalog/tags",
            json={"name": "t"},
            headers=auth,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "noop"

    def test_add_returns_reactivated_on_inactive_name(
        self,
        client,
        enable_admin,
        auth,
    ):
        client.post("/api/admin/catalog/tags", json={"name": "t"}, headers=auth)
        # Deactivate via PATCH.
        tid = next(t["id"] for t in client.get("/api/catalog").json()["tags"] if t["name"] == "t")
        client.patch(
            f"/api/admin/catalog/tags/{tid}",
            json={"is_active": False},
            headers=auth,
        )
        resp = client.post(
            "/api/admin/catalog/tags",
            json={"name": "t"},
            headers=auth,
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "reactivated"


@allure.epic("API")
@allure.feature("Admin catalog — reload-map")
class TestReloadMap:
    def test_reload_disabled_without_token(self, client, monkeypatch):
        monkeypatch.setattr(settings, "admin_api_token", "")
        resp = client.post("/api/admin/reload-map")
        assert resp.status_code == 503

    def test_reload_503_when_spreadsheet_unset(
        self,
        client,
        enable_admin,
        auth,
        monkeypatch,
    ):
        # Empty sheet_logging_spreadsheet => service not available,
        # not "bad request".
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        resp = client.post("/api/admin/reload-map", headers=auth)
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_reload_validation_error_surfaces_as_400(
        self,
        client,
        enable_admin,
        auth,
        monkeypatch,
    ):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "FAKE_SSID")

        def fail(*_a, **_kw):
            raise runtime_map.MapTabError("bad row 3: unknown category 'xxx'")

        with patch.object(runtime_map, "reload_now", side_effect=fail):
            resp = client.post("/api/admin/reload-map", headers=auth)
        assert resp.status_code == 400
        assert "unknown category" in resp.json()["detail"]
