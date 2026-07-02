"""Tests for ``GET /api/catalog`` — 3D catalog snapshot with ETag support, see
``specs/reference/catalog-api.md``."""

import shutil

import allure
import pytest

from dinary.api.controllers.catalog import if_none_match_matches as _if_none_match_matches
from dinary.db import storage


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch, blank_db):
    dst = tmp_path / "dinary.db"
    shutil.copy(blank_db, dst)
    monkeypatch.setattr(storage, "DATA_DIR", tmp_path)
    monkeypatch.setattr(storage, "DB_PATH", dst)
    con = storage.get_connection()
    try:
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (1, 'Food', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active)"
            " VALUES (2, 'RetiredGroup', 2, FALSE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active) VALUES (1, 'food', 1, TRUE)",
        )
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active)"
            " VALUES (2, 'retired', 1, FALSE)",
        )
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (1, 'tag_a', TRUE)",
        )
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to,"
            " auto_attach_enabled, is_active)"
            " VALUES (1, 'evt', '2026-01-01', '2026-12-31', TRUE, TRUE)",
        )
    finally:
        con.close()


@allure.epic("Catalog")
@allure.feature("API")
class TestCatalogGet:
    def test_returns_shape(self, client):
        resp = client.get("/api/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "catalog_version" in data
        assert "etag" not in data
        assert resp.headers["ETag"].startswith('W/"catalog-v')
        # Every row is surfaced, active and inactive alike; PWA
        # filters client-side so it can toggle "Show inactive".
        groups = {g["name"]: g["is_active"] for g in data["category_groups"]}
        assert groups == {"Food": True, "RetiredGroup": False}
        cats = {c["name"]: c["is_active"] for c in data["categories"]}
        assert cats == {"food": True, "retired": False}
        assert [t["name"] for t in data["tags"]] == ["tag_a"]
        events = data["events"]
        assert [e["name"] for e in events] == ["evt"]
        assert events[0]["auto_attach_enabled"] is True
        assert events[0]["is_active"] is True
        assert events[0]["auto_tags"] == []
        # frequent_categories must be present (may be empty when no manual expenses exist)
        assert "frequent_categories" in data
        assert isinstance(data["frequent_categories"], list)

    def test_304_on_matching_etag(self, client):
        first = client.get("/api/catalog")
        etag = first.headers["ETag"]
        second = client.get("/api/catalog", headers={"If-None-Match": etag})
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers.get("ETag") == etag

    def test_full_payload_on_stale_etag(self, client):
        resp = client.get("/api/catalog", headers={"If-None-Match": 'W/"catalog-v0"'})
        assert resp.status_code == 200
        assert resp.headers["ETag"].startswith('W/"catalog-v')

    def test_304_on_comma_separated_list_containing_match(self, client):
        """RFC 7232: ``If-None-Match`` is a list. Proxies and curl
        callers can legitimately replay every tag they've ever seen;
        returning 304 on any list member keeps the cache working."""
        fresh = client.get("/api/catalog").headers["ETag"]
        header = f'W/"catalog-v0", {fresh}, W/"catalog-v999"'
        resp = client.get("/api/catalog", headers={"If-None-Match": header})
        assert resp.status_code == 304
        assert resp.content == b""

    def test_200_when_list_has_no_match(self, client):
        header = 'W/"catalog-v0", W/"catalog-v999"'
        resp = client.get("/api/catalog", headers={"If-None-Match": header})
        assert resp.status_code == 200

    def test_304_on_wildcard_if_none_match(self, client):
        """``If-None-Match: *`` means "as long as a representation
        exists, don't re-send it" — the catalog always exists, so we
        always short-circuit to 304."""
        resp = client.get("/api/catalog", headers={"If-None-Match": "*"})
        assert resp.status_code == 304
        assert resp.content == b""


@allure.epic("Catalog")
@allure.feature("API")
class TestCatalogRemovableFlag:
    """``removable`` is true exactly when a DELETE would hard-delete, see
    ``specs/reference/catalog-api.md``."""

    def test_unreferenced_leaf_rows_are_removable(self, client):
        # Categories, events, and tags in the fixture have no
        # references at all, so they are all hard-deletable. Groups
        # are tested separately because group 1 has child categories.
        data = client.get("/api/catalog").json()
        for key in ("categories", "events", "tags"):
            for row in data[key]:
                assert row["removable"] is True, (key, row)
        # Childless group is removable; group-with-children is not.
        groups = {g["id"]: g["removable"] for g in data["category_groups"]}
        assert groups[1] is False
        assert groups[2] is True

    def test_category_becomes_non_removable_when_referenced_by_expense(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO expenses (id, client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (1, 'e1', '2026-04-21 12:00:00', 10.0, 10.0, 'RSD', 1)",
            )
        finally:
            con.close()
        data = client.get("/api/catalog").json()
        cats = {c["id"]: c["removable"] for c in data["categories"]}
        # Referenced category: no longer hard-deletable.
        assert cats[1] is False
        # Sibling unreferenced category stays removable.
        assert cats[2] is True

    def test_event_becomes_non_removable_when_referenced_by_expense(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO expenses (id, client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id, event_id)"
                " VALUES (10, 'e10', '2026-04-21 12:00:00', 10.0, 10.0, 'RSD', 1, 1)",
            )
        finally:
            con.close()
        data = client.get("/api/catalog").json()
        events = {ev["id"]: ev["removable"] for ev in data["events"]}
        assert events[1] is False

    def test_tag_non_removable_if_in_any_event_auto_tags(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO tags (id, name, is_active) VALUES (99, 'vacation', TRUE)",
            )
            con.execute(
                "UPDATE events SET auto_tags = '[99]' WHERE id = 1",
            )
        finally:
            con.close()
        data = client.get("/api/catalog").json()
        tags = {t["id"]: t["removable"] for t in data["tags"]}
        assert tags[99] is False
        # Pre-existing unrelated tag stays removable.
        assert tags[1] is True


@allure.epic("Catalog")
@allure.feature("API")
class TestIfNoneMatchUnit:
    """Direct unit coverage for the list/wildcard parser — cheaper to pin edge
    cases here than through repeated ``client.get`` calls."""

    def test_empty_header_is_not_a_match(self):

        assert _if_none_match_matches("", 'W/"catalog-v1"') is False
        assert _if_none_match_matches("   ", 'W/"catalog-v1"') is False

    def test_wildcard_matches(self):

        assert _if_none_match_matches("*", 'W/"catalog-v1"') is True

    def test_exact_single_tag_matches(self):

        assert _if_none_match_matches('W/"catalog-v1"', 'W/"catalog-v1"') is True
        assert _if_none_match_matches('W/"catalog-v2"', 'W/"catalog-v1"') is False

    def test_comma_separated_list_member_matches(self):

        header = 'W/"catalog-v0", W/"catalog-v1", W/"catalog-v2"'
        assert _if_none_match_matches(header, 'W/"catalog-v1"') is True

    def test_comma_separated_list_without_match(self):

        header = 'W/"catalog-v0", W/"catalog-v2"'
        assert _if_none_match_matches(header, 'W/"catalog-v1"') is False

    def test_whitespace_around_tags_is_tolerated(self):

        header = '   W/"catalog-v1"   ,   W/"catalog-v2"   '
        assert _if_none_match_matches(header, 'W/"catalog-v2"') is True


@allure.epic("Catalog")
@allure.feature("API")
class TestFrequentCategories:
    """frequent_categories counts only manually-entered expenses (receipt_id IS NULL)
    from the past 3 months, ordered by count descending, capped at 5."""

    def test_receipt_expenses_are_excluded(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO receipts (id, client_receipt_id, url) VALUES (1, 'r1', 'https://x')"
            )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id, receipt_id)"
                " VALUES ('re1', datetime('now'), 10.0, 10.0, 'RSD', 1, 1)"
            )
        finally:
            con.close()

        data = client.get("/api/catalog").json()
        assert data["frequent_categories"] == [], (
            "receipt-backed expenses must not appear in frequent categories"
        )

    def test_old_expenses_excluded(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id)"
                " VALUES ('old1', datetime('now', '-4 months'), 10.0, 10.0, 'RSD', 1)"
            )
        finally:
            con.close()

        data = client.get("/api/catalog").json()
        assert data["frequent_categories"] == [], "expenses older than 3 months must be excluded"

    def test_ordered_by_count_desc(self, client):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (3, 'Extra', 3, TRUE)"
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (5, 'popular', 3, TRUE)"
            )
            for i in range(3):
                con.execute(
                    f"INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"  # noqa: S608
                    f" currency_original, category_id)"
                    f" VALUES ('pop-{i}', datetime('now'), 10.0, 10.0, 'RSD', 5)"
                )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount, amount_original,"
                " currency_original, category_id)"
                " VALUES ('cat1-one', datetime('now'), 10.0, 10.0, 'RSD', 1)"
            )
        finally:
            con.close()

        data = client.get("/api/catalog").json()
        freq = data["frequent_categories"]
        assert len(freq) >= 2
        assert freq[0]["id"] == 5, "category with 3 expenses must rank first"
        assert freq[1]["id"] == 1, "category with 1 expense must rank second"
