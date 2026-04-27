"""PATCH /api/admin/catalog/<kind>/<id> tests.

Pin the patch-side surface: combined rename + deactivate atomically,
soft-retire of referenced rows, the tag-rename cascade into
``events.auto_tags`` (which stores names rather than ids and would
otherwise silently break the auto-attach pipeline on every rename),
and the reactivation affordance that flips ``is_active`` back to
``TRUE``.

Sibling files cover add (:file:`test_admin_catalog_add.py`),
delete (:file:`test_admin_catalog_delete.py`), and version /
reload-map plumbing (:file:`test_admin_catalog_meta.py`).
"""

from datetime import datetime

import allure

from dinary.services import ledger_repo

from _admin_catalog_helpers import _tmp_db  # noqa: F401  (autouse)


@allure.epic("API")
@allure.feature("Admin catalog — patch")
class TestAdminPatch:
    def test_patch_rename_and_deactivate_in_one_call(self, client):
        create = client.post(
            "/api/admin/catalog/categories",
            json={"name": "orig", "group_id": 1},
        )
        assert create.status_code == 200
        cid = create.json()["new_id"]
        v_before = create.json()["catalog_version"]

        patch_resp = client.patch(
            f"/api/admin/catalog/categories/{cid}",
            json={"name": "renamed", "is_active": False},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()
        assert data["catalog_version"] == v_before + 1
        # GET /api/catalog now returns every row; the inactive one is
        # still present but flagged ``is_active=False`` so the PWA can
        # surface it under the per-picker "Показать неактивные" toggle.
        entry = next(c for c in data["categories"] if c["id"] == cid)
        assert entry["is_active"] is False
        assert entry["name"] == "renamed"

    def test_patch_soft_retire_referenced_category_succeeds(self, client):
        """PATCH ``is_active=False`` on a referenced row now succeeds
        (soft-retire), matching DELETE's semantics on the same row.
        The previous 409-on-in-use asymmetry between PATCH and DELETE
        was removed; operators use PATCH to flip the flag in either
        direction and DELETE to actually try to remove the row."""
        create = client.post(
            "/api/admin/catalog/categories",
            json={"name": "pinned", "group_id": 1},
        )
        cid = create.json()["new_id"]
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
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
            json={"is_active": False},
        )
        assert resp.status_code == 200, resp.text

        snap = client.get("/api/catalog").json()
        assert any(
            c["id"] == cid and c["name"] == "pinned" and c["is_active"] is False
            for c in snap["categories"]
        )

    def test_patch_rename_tag_cascades_into_events_auto_tags(self, client):
        """Renaming a tag via PATCH must rewrite every ``events.auto_tags``
        entry that references the old name.

        ``auto_tags`` stores tag *names* (not ids) so the JSON value is
        stable across seed rebuilds that renumber ids, but that means a
        plain ``UPDATE tags SET name = ?`` would silently break the
        auto-attach pipeline: the next ``resolve_event_auto_tag_ids``
        call would log an "unknown tag name" WARN and drop the tag
        from every new expense created under that event. The cascade
        keeps auto-attach behaviour identical across the rename.
        """
        tag = client.post("/api/admin/catalog/tags", json={"name": "oldname"})
        tid = tag.json()["new_id"]
        ev = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "evt-with-tag",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "auto_tags": ["oldname"],
            },
        )
        eid = ev.json()["new_id"]
        resp = client.patch(
            f"/api/admin/catalog/tags/{tid}",
            json={"name": "newname"},
        )
        assert resp.status_code == 200, resp.text
        con = ledger_repo.get_connection()
        try:
            row = con.execute(
                "SELECT auto_tags FROM events WHERE id = ?",
                [eid],
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        raw = row[0] or ""
        assert "newname" in raw
        assert "oldname" not in raw

    def test_patch_reactivates_soft_deleted_tag(self, client):
        add = client.post("/api/admin/catalog/tags", json={"name": "retired"})
        tid = add.json()["new_id"]
        client.patch(
            f"/api/admin/catalog/tags/{tid}",
            json={"is_active": False},
        )
        snap = client.get("/api/catalog").json()
        assert not any(t["id"] == tid and t["is_active"] for t in snap["tags"])

        # PATCH is_active=True is the PWA's "Активировать" affordance.
        resp = client.patch(
            f"/api/admin/catalog/tags/{tid}",
            json={"is_active": True},
        )
        assert resp.status_code == 200
        assert any(t["id"] == tid and t["is_active"] is True for t in resp.json()["tags"])
