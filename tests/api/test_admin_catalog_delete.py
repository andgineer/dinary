"""DELETE /api/catalog/<kind>/<id> tests — soft-vs-hard delete decision, see
``specs/reference/catalog-api.md``. Sibling files cover add, patch, and version
plumbing."""

from datetime import datetime

import allure

from dinary.db import storage
from dinary.db.expenses import ExpensePayload, insert_expense

from _admin_catalog_helpers import db  # noqa: F401  (autouse)


@allure.epic("Catalog")
@allure.feature("Admin API")
class TestAdminDelete:
    def test_delete_unused_tag_is_hard(self, client):
        add = client.post("/api/catalog/tags", json={"name": "drop-me"})
        tid = add.json()["tag"]["id"]
        resp = client.delete(f"/api/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "hard"
        assert data["usage_count"] == 0
        tags = client.get("/api/catalog").json()["tags"]
        assert not any(t["id"] == tid for t in tags)

    def test_delete_used_tag_is_soft(self, client):
        add = client.post("/api/catalog/tags", json={"name": "pinned-tag"})
        tid = add.json()["tag"]["id"]
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'cat-for-tag', 1, TRUE)",
            )
            cid = 1
            insert_expense(
                con,
                ExpensePayload(
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
                ),
                enqueue_logging=False,
            )
        finally:
            con.close()
        resp = client.delete(f"/api/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] >= 1
        tags = client.get("/api/catalog").json()["tags"]
        entry = next(t for t in tags if t["id"] == tid)
        assert entry["is_active"] is False

    def test_delete_group_hard_when_empty(self, client):
        group = client.post(
            "/api/catalog/groups",
            json={"name": "EmptyGroup"},
        )
        gid = group.json()["group"]["id"]
        resp = client.delete(f"/api/catalog/groups/{gid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "hard"
        groups = client.get("/api/catalog").json()["category_groups"]
        assert not any(g["id"] == gid for g in groups)

    def test_delete_group_refuses_while_it_has_categories(self, client):
        group = client.post(
            "/api/catalog/groups",
            json={"name": "Blocked"},
        )
        gid = group.json()["group"]["id"]
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'tenant', ?, TRUE)",
                [gid],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/catalog/groups/{gid}")
        # A group that still contains any category (active or not) can't be
        # deleted; the operator must first soft/hard-delete every category.
        assert resp.status_code == 409

    def test_delete_tag_referenced_by_sheet_mapping_tags_is_soft(self, client):
        tag = client.post("/api/catalog/tags", json={"name": "mapped-tag"})
        tid = tag.json()["tag"]["id"]
        con = storage.get_connection()
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
        resp = client.delete(f"/api/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] == 0
        tags = client.get("/api/catalog").json()["tags"]
        assert any(t["id"] == tid and t["is_active"] is False for t in tags)

    def test_delete_tag_referenced_only_by_events_auto_tags_is_soft(self, client):
        """``events.auto_tags`` (a JSON integer array of tag IDs) also counts
        as a mapping-side reference for the hard-vs-soft decision.
        Without this guard, deleting a tag that is only referenced by an
        event's ``auto_tags`` would remove the ``tags`` row but leave
        an orphan ID in the event.
        """
        tag = client.post("/api/catalog/tags", json={"name": "auto-only"})
        tid = tag.json()["tag"]["id"]
        ev = client.post(
            "/api/catalog/events",
            json={
                "name": "trip-with-auto-tag",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
                "auto_tags": [tid],
            },
        )
        eid = ev.json()["event"]["id"]
        resp = client.delete(f"/api/catalog/tags/{tid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] == 0
        tags = client.get("/api/catalog").json()["tags"]
        assert any(t["id"] == tid and t["is_active"] is False for t in tags)
        con = storage.get_connection()
        try:
            row = con.execute(
                "SELECT auto_tags FROM events WHERE id = ?",
                [eid],
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert str(tid) in (row[0] or "")

    def test_delete_event_referenced_by_sheet_mapping_is_soft(self, client):
        ev = client.post(
            "/api/catalog/events",
            json={
                "name": "mapped-event",
                "date_from": "2026-01-01",
                "date_to": "2026-12-31",
            },
        )
        eid = ev.json()["event"]["id"]
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO sheet_mapping"
                " (row_order, category_id, event_id, sheet_category, sheet_group)"
                " VALUES (1, NULL, ?, '*', '*')",
                [eid],
            )
        finally:
            con.close()
        resp = client.delete(f"/api/catalog/events/{eid}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["delete_status"] == "soft"
        assert data["usage_count"] == 0
        events = client.get("/api/catalog").json()["events"]
        assert any(e["id"] == eid and e["is_active"] is False for e in events)
