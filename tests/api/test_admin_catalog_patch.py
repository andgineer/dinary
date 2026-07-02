"""PATCH /api/catalog/<kind>/<id> tests: ``events.auto_tags`` stores IDs and
stays unchanged by a tag rename, and the reactivation affordance flips
``is_active`` back to TRUE. Sibling files cover add, delete, and version plumbing."""

import allure

from dinary.db import storage

from _admin_catalog_helpers import db  # noqa: F401  (autouse)


@allure.epic("Catalog")
@allure.feature("Admin API")
class TestAdminPatch:
    def test_patch_rename_tag_leaves_events_auto_tags_unchanged(self, client):
        """Renaming a tag must NOT change events.auto_tags: stored IDs are stable
        across renames so no cascade rewrite is needed."""
        tag = client.post("/api/catalog/tags", json={"name": "oldname"})
        tid = tag.json()["tag"]["id"]
        ev = client.post(
            "/api/catalog/events",
            json={
                "name": "evt-with-tag",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "auto_tags": [tid],
            },
        )
        eid = ev.json()["event"]["id"]
        resp = client.patch(
            f"/api/catalog/tags/{tid}",
            json={"name": "newname"},
        )
        assert resp.status_code == 200, resp.text
        con = storage.get_connection()
        try:
            row = con.execute(
                "SELECT auto_tags FROM events WHERE id = ?",
                [eid],
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        raw = row[0] or ""
        assert str(tid) in raw

    def test_patch_reactivates_soft_deleted_tag(self, client):
        add = client.post("/api/catalog/tags", json={"name": "retired"})
        tid = add.json()["tag"]["id"]
        client.patch(
            f"/api/catalog/tags/{tid}",
            json={"is_active": False},
        )
        snap = client.get("/api/catalog").json()
        assert not any(t["id"] == tid and t["is_active"] for t in snap["tags"])

        # PATCH is_active=True is the PWA's "Activate" affordance.
        resp = client.patch(
            f"/api/catalog/tags/{tid}",
            json={"is_active": True},
        )
        assert resp.status_code == 200

        tags = client.get("/api/catalog").json()["tags"]
        assert any(t["id"] == tid and t["is_active"] is True for t in tags)
