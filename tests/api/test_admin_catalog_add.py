"""POST /api/catalog/<kind> tests.

Pin the add-side surface: snapshot return shape (so the PWA can swap
its cache without a second round-trip), the ``ETag`` header for the
new ``catalog_version``, the ``status`` field that distinguishes
``created`` / ``noop`` / ``reactivated``, and the date-range
validation on event creation.

Sibling files cover patch (:file:`test_admin_catalog_patch.py`),
delete (:file:`test_admin_catalog_delete.py`), and version /
reload-map plumbing (:file:`test_admin_catalog_meta.py`).
"""

import allure

from _admin_catalog_helpers import db  # noqa: F401  (autouse)


@allure.epic("Catalog")
@allure.feature("Admin API")
class TestAdminAdd:
    def test_add_tag_returns_snapshot(self, client):
        resp = client.post("/api/catalog/tags", json={"name": "t1"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["tag"]["id"] >= 1
        assert data["tag"]["name"] == "t1"
        assert "etag" not in data
        assert resp.headers["ETag"].startswith('W/"catalog-v')

    def test_add_event_with_range(self, client):
        resp = client.post(
            "/api/catalog/events",
            json={
                "name": "trip-2026",
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["event"]["name"] == "trip-2026"

    def test_add_event_with_auto_tags(self, client):
        tag = client.post("/api/catalog/tags", json={"name": "путешествия"})
        tid = tag.json()["tag"]["id"]
        resp = client.post(
            "/api/catalog/events",
            json={
                "name": "отпуск-Доломиты",
                "date_from": "2026-07-01",
                "date_to": "2026-07-15",
                "auto_attach_enabled": True,
                "auto_tags": [tid],
            },
        )
        assert resp.status_code == 200, resp.text
        ev = resp.json()["event"]
        assert ev["name"] == "отпуск-Доломиты"
        assert ev["auto_tags"] == [tid]
        assert ev["auto_attach_enabled"] is True

    def test_add_event_rejects_bad_range(self, client):
        resp = client.post(
            "/api/catalog/events",
            json={
                "name": "bad",
                "date_from": "2026-06-30",
                "date_to": "2026-06-01",
            },
        )
        assert resp.status_code == 422


@allure.epic("Catalog")
@allure.feature("Admin API")
class TestAddStatus:
    def test_add_returns_created_on_new_name(self, client):
        resp = client.post("/api/catalog/tags", json={"name": "fresh"})
        assert resp.json()["status"] == "created"

    def test_add_returns_noop_on_existing_active_name(self, client):
        client.post("/api/catalog/tags", json={"name": "t"})
        resp = client.post("/api/catalog/tags", json={"name": "t"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "noop"

    def test_add_returns_reactivated_on_inactive_name(self, client):
        client.post("/api/catalog/tags", json={"name": "t"})
        tid = next(t["id"] for t in client.get("/api/catalog").json()["tags"] if t["name"] == "t")
        client.patch(
            f"/api/catalog/tags/{tid}",
            json={"is_active": False},
        )
        resp = client.post("/api/catalog/tags", json={"name": "t"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "reactivated"
