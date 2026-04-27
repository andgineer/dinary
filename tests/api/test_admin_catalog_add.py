"""POST /api/admin/catalog/<kind> tests.

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

from _admin_catalog_helpers import _tmp_db  # noqa: F401  (autouse)


@allure.epic("API")
@allure.feature("Admin catalog — add")
class TestAdminAdd:
    def test_add_tag_returns_snapshot(self, client):
        resp = client.post("/api/admin/catalog/tags", json={"name": "t1"})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["new_id"] >= 1
        assert {t["name"] for t in data["tags"]} == {"t1"}
        assert "etag" not in data
        assert resp.headers["ETag"].startswith('W/"catalog-v')

    def test_add_group_then_category(self, client):
        g = client.post(
            "/api/admin/catalog/groups",
            json={"name": "Transport"},
        )
        assert g.status_code == 200
        gid = g.json()["new_id"]
        c = client.post(
            "/api/admin/catalog/categories",
            json={"name": "metro", "group_id": gid},
        )
        assert c.status_code == 200, c.text
        assert any(cat["name"] == "metro" for cat in c.json()["categories"])

    def test_add_event_with_range(self, client):
        resp = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "trip-2026",
                "date_from": "2026-06-01",
                "date_to": "2026-06-30",
            },
        )
        assert resp.status_code == 200, resp.text
        events = resp.json()["events"]
        assert any(e["name"] == "trip-2026" for e in events)

    def test_add_event_with_auto_tags(self, client):
        client.post("/api/admin/catalog/tags", json={"name": "путешествия"})
        resp = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "отпуск-Доломиты",
                "date_from": "2026-07-01",
                "date_to": "2026-07-15",
                "auto_attach_enabled": True,
                "auto_tags": ["путешествия"],
            },
        )
        assert resp.status_code == 200, resp.text
        ev = next(e for e in resp.json()["events"] if e["name"] == "отпуск-Доломиты")
        assert ev["auto_tags"] == ["путешествия"]
        assert ev["auto_attach_enabled"] is True

    def test_add_event_rejects_bad_range(self, client):
        resp = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "bad",
                "date_from": "2026-06-30",
                "date_to": "2026-06-01",
            },
        )
        assert resp.status_code == 422


@allure.epic("API")
@allure.feature("Admin catalog — add status")
class TestAddStatus:
    def test_add_returns_created_on_new_name(self, client):
        resp = client.post("/api/admin/catalog/tags", json={"name": "fresh"})
        assert resp.json()["status"] == "created"

    def test_add_returns_noop_on_existing_active_name(self, client):
        client.post("/api/admin/catalog/tags", json={"name": "t"})
        resp = client.post("/api/admin/catalog/tags", json={"name": "t"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "noop"

    def test_add_returns_reactivated_on_inactive_name(self, client):
        client.post("/api/admin/catalog/tags", json={"name": "t"})
        tid = next(t["id"] for t in client.get("/api/catalog").json()["tags"] if t["name"] == "t")
        client.patch(
            f"/api/admin/catalog/tags/{tid}",
            json={"is_active": False},
        )
        resp = client.post("/api/admin/catalog/tags", json={"name": "t"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "reactivated"
