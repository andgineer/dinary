"""DELETE /api/admin/catalog/<kind>/<id> tests.

Pin the soft-vs-hard delete decision and the ``delete_status``
field that surfaces it. Soft-delete is triggered by *any*
referencing row, including:

* ``expense_*`` ledger references (``usage_count >= 1``);
* ``sheet_mapping`` / ``sheet_mapping_tags`` projection rules;
* ``events.auto_tags`` JSON name pointers (a tag-only,
  ``usage_count == 0`` reference that would silently rot if the row
  were hard-deleted).

A group with surviving categories cannot be deleted at all (409) —
the operator must drain the group first.

Sibling files cover add (:file:`test_admin_catalog_add.py`),
patch (:file:`test_admin_catalog_patch.py`), and version /
reload-map plumbing (:file:`test_admin_catalog_meta.py`).
"""

from datetime import datetime

import allure

from dinary.services import ledger_repo

from _admin_catalog_helpers import db  # noqa: F401  (autouse)


@allure.epic("API")
@allure.feature("Admin catalog — delete")
class TestAdminDelete:
    def test_delete_unused_tag_is_hard(self, client):
        add = client.post("/api/admin/catalog/tags", json={"name": "drop-me"})
        tid = add.json()["new_id"]
        resp = client.delete(f"/api/admin/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "hard"
        assert data["usage_count"] == 0
        assert not any(t["id"] == tid for t in data["tags"])

    def test_delete_used_tag_is_soft(self, client):
        add = client.post("/api/admin/catalog/tags", json={"name": "pinned-tag"})
        tid = add.json()["new_id"]
        cat = client.post(
            "/api/admin/catalog/categories",
            json={"name": "cat-for-tag", "group_id": 1},
        )
        cid = cat.json()["new_id"]
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="tag-soft-1",
                expense_datetime=datetime(2026, 4, 20, 10, 0, 0),
                amount=1.0,
                amount_original=1.0,
                currency_original="RSD",
                category_id=cid,
                event_id=None,
                comment="",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[tid],
                enqueue_logging=False,
            )
        finally:
            con.close()
        resp = client.delete(f"/api/admin/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] >= 1
        entry = next(t for t in data["tags"] if t["id"] == tid)
        assert entry["is_active"] is False

    def test_delete_category_soft_when_used(self, client):
        cat = client.post(
            "/api/admin/catalog/categories",
            json={"name": "pinned-cat", "group_id": 1},
        )
        cid = cat.json()["new_id"]
        con = ledger_repo.get_connection()
        try:
            ledger_repo.insert_expense(
                con,
                client_expense_id="cat-soft-1",
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
        resp = client.delete(f"/api/admin/catalog/categories/{cid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert any(c["id"] == cid and c["is_active"] is False for c in data["categories"])

    def test_delete_group_hard_when_empty(self, client):
        group = client.post(
            "/api/admin/catalog/groups",
            json={"name": "EmptyGroup"},
        )
        gid = group.json()["new_id"]
        resp = client.delete(f"/api/admin/catalog/groups/{gid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "hard"
        assert not any(g["id"] == gid for g in data["category_groups"])

    def test_delete_group_refuses_while_it_has_categories(self, client):
        group = client.post(
            "/api/admin/catalog/groups",
            json={"name": "Blocked"},
        )
        gid = group.json()["new_id"]
        client.post(
            "/api/admin/catalog/categories",
            json={"name": "tenant", "group_id": gid},
        )
        resp = client.delete(f"/api/admin/catalog/groups/{gid}")
        # A group that still contains any category (active or not) can't be
        # deleted; the operator must first soft/hard-delete every category.
        assert resp.status_code == 409

    def test_delete_category_referenced_by_sheet_mapping_is_soft(self, client):
        """``sheet_mapping`` carries a FK into ``categories``. Even when
        no expense row references the category, a surviving
        ``sheet_mapping`` row would cause the hard DELETE to trip the
        FK constraint — the writer must detect that and soft-delete
        instead so the drain loop's atomic map-swap is the single
        place responsible for sheet_mapping churn.
        """
        cat = client.post(
            "/api/admin/catalog/categories",
            json={"name": "mapped-only", "group_id": 1},
        )
        cid = cat.json()["new_id"]
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO sheet_mapping"
                " (row_order, category_id, event_id, sheet_category, sheet_group)"
                " VALUES (1, ?, NULL, '*', '*')",
                [cid],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/admin/catalog/categories/{cid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        # ``usage_count`` is the ledger count (expenses) only, NOT the
        # mapping reference count — it's used to tell the operator
        # "used by N expenses" and must stay 0 here.
        assert data["usage_count"] == 0
        assert any(c["id"] == cid and c["is_active"] is False for c in data["categories"])

    def test_delete_tag_referenced_by_sheet_mapping_tags_is_soft(self, client):
        tag = client.post("/api/admin/catalog/tags", json={"name": "mapped-tag"})
        tid = tag.json()["new_id"]
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO sheet_mapping"
                " (row_order, category_id, event_id, sheet_category, sheet_group)"
                " VALUES (1, NULL, NULL, '*', '*')",
            )
            con.execute(
                "INSERT INTO sheet_mapping_tags (mapping_row_order, tag_id) VALUES (1, ?)",
                [tid],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/admin/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] == 0
        assert any(t["id"] == tid and t["is_active"] is False for t in data["tags"])

    def test_delete_tag_referenced_only_by_events_auto_tags_is_soft(self, client):
        """``events.auto_tags`` (a JSON array of tag names) also counts
        as a mapping-side reference for the hard-vs-soft decision.
        Without this guard, deleting a tag that is only named by an
        event's ``auto_tags`` would remove the ``tags`` row but leave
        an orphan name pointer in the event; subsequent
        ``resolve_event_auto_tag_ids`` calls would silently drop it
        (logging a WARN), which is a data-loss footgun operators
        can't recover from via the admin UI.
        """
        tag = client.post("/api/admin/catalog/tags", json={"name": "auto-only"})
        tid = tag.json()["new_id"]
        ev = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "trip-with-auto-tag",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "auto_tags": ["auto-only"],
            },
        )
        eid = ev.json()["new_id"]
        resp = client.delete(f"/api/admin/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # Only events.auto_tags references the tag — not sheet_mapping_tags,
        # not import_mapping_tags, not expense_tags — yet the writer must
        # still soft-delete to preserve the auto_tags name pointer.
        assert data["delete_status"] == "soft"
        assert data["usage_count"] == 0
        assert any(t["id"] == tid and t["is_active"] is False for t in data["tags"])
        # auto_tags name list on the event is unchanged: the tag row is
        # retired (is_active=FALSE) but still reachable by id, and the
        # name reference in events.auto_tags stays intact.
        con = ledger_repo.get_connection()
        try:
            row = con.execute(
                "SELECT auto_tags FROM events WHERE id = ?",
                [eid],
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert "auto-only" in (row[0] or "")

    def test_delete_event_referenced_by_sheet_mapping_is_soft(self, client):
        ev = client.post(
            "/api/admin/catalog/events",
            json={
                "name": "mapped-event",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
        )
        eid = ev.json()["new_id"]
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO sheet_mapping"
                " (row_order, category_id, event_id, sheet_category, sheet_group)"
                " VALUES (1, NULL, ?, '*', '*')",
                [eid],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/admin/catalog/events/{eid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] == 0
        assert any(e["id"] == eid and e["is_active"] is False for e in data["events"])
