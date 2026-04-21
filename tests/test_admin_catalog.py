"""Tests for admin catalog API endpoints.

The admin surface currently has **no authentication**: the shared
``DINARY_ADMIN_API_TOKEN`` gate was removed pending a real auth
layer, so every endpoint is reachable by anyone who can reach the
server (the deployment must put it behind a private network). These
tests therefore only pin:

* POST /api/admin/catalog/<kind> returns the full catalog snapshot
  embedded in the response body so the PWA can swap its cache without
  a second round-trip, plus an ``ETag`` header for the new
  ``catalog_version``.
* PATCH supports combined rename + deactivate atomically and rolls
  back the name change when deactivation fails because the row is
  still referenced by an expense.
* DELETE soft-deletes items that are still referenced (``is_active``
  flipped to FALSE) and hard-deletes unreferenced rows, reporting
  which outcome via ``delete_status``.
* Adding a name that already exists but is inactive flips
  ``is_active`` back on and reports ``status="reactivated"``.
* ``/api/admin/reload-map`` swaps the ``sheet_mapping`` table and
  surfaces ``MapTabError`` as a 400.
"""

from datetime import datetime
from unittest.mock import patch

import allure
import pytest

from dinary.config import settings
from dinary.services import duckdb_repo, sheet_mapping


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
        con = duckdb_repo.get_connection()
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
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
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
        con = duckdb_repo.get_connection()
        try:
            duckdb_repo.insert_expense(
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
        con = duckdb_repo.get_connection()
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
        con = duckdb_repo.get_connection()
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
        con = duckdb_repo.get_connection()
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
        con = duckdb_repo.get_connection()
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


@allure.epic("API")
@allure.feature("Admin catalog — catalog_version end-to-end")
class TestCatalogVersionE2E:
    def test_add_bumps_catalog_version_visible_in_get_catalog(self, client):
        v0 = client.get("/api/catalog").json()["catalog_version"]
        client.post("/api/admin/catalog/tags", json={"name": "brand-new-tag"})
        snap_resp = client.get("/api/catalog")
        snap = snap_resp.json()
        assert snap["catalog_version"] == v0 + 1
        assert any(t["name"] == "brand-new-tag" for t in snap["tags"])
        assert snap_resp.headers["ETag"] == f'W/"catalog-v{v0 + 1}"'


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


@allure.epic("API")
@allure.feature("Admin catalog — reload-map")
class TestReloadMap:
    def test_reload_503_when_spreadsheet_unset(self, client, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "")
        resp = client.post("/api/admin/reload-map")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"]

    def test_reload_validation_error_surfaces_as_400(self, client, monkeypatch):
        monkeypatch.setattr(settings, "sheet_logging_spreadsheet", "FAKE_SSID")

        def fail(*_a, **_kw):
            raise sheet_mapping.MapTabError("bad row 3: unknown category 'xxx'")

        with patch.object(sheet_mapping, "reload_now", side_effect=fail):
            resp = client.post("/api/admin/reload-map")
        assert resp.status_code == 400
        assert "unknown category" in resp.json()["detail"]
