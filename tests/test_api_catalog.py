"""Tests for ``GET /api/catalog`` — 3D catalog snapshot with ETag support.

The PWA relies on two invariants:

1. The snapshot shape (``category_groups``, ``categories``,
   ``events``, ``tags``) matches exactly one primary-key-carrying item
   per row; groups with ``is_active=FALSE`` are hidden.
2. ``If-None-Match`` matching the current ETag returns 304 with
   empty body; a mismatch returns the full payload plus a new
   ``ETag`` header. The ETag rides on the HTTP header only — the
   response body does not duplicate it.

A broken ETag path would turn every catalog refresh into a full
payload download, silently undoing the Phase 2 cache design.
"""

import allure
import pytest

from dinary.services import duckdb_repo


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


@allure.epic("API")
@allure.feature("Catalog (3D) — snapshot + ETag")
class TestCatalogGet:
    def test_returns_shape(self, client):
        resp = client.get("/api/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "catalog_version" in data
        # ETag lives on the HTTP header, not in the body.
        assert "etag" not in data
        assert resp.headers["ETag"].startswith('W/"catalog-v')
        group_names = {g["name"] for g in data["category_groups"]}
        assert group_names == {"Food"}  # inactive group hidden
        cat_names = {c["name"] for c in data["categories"]}
        assert cat_names == {"food"}  # inactive cat hidden
        assert [t["name"] for t in data["tags"]] == ["tag_a"]
        assert [e["name"] for e in data["events"]] == ["evt"]

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


@allure.epic("API")
@allure.feature("Catalog (3D) — If-None-Match parsing")
class TestIfNoneMatchUnit:
    """Direct unit coverage for the list/wildcard parser. The
    integration tests above exercise it end-to-end, but the parser is
    pure and small enough that edge cases are cheaper to pin down here
    than through repeated ``client.get`` calls."""

    def test_empty_header_is_not_a_match(self):
        from dinary.api.catalog import _if_none_match_matches

        assert _if_none_match_matches("", 'W/"catalog-v1"') is False
        assert _if_none_match_matches("   ", 'W/"catalog-v1"') is False

    def test_wildcard_matches(self):
        from dinary.api.catalog import _if_none_match_matches

        assert _if_none_match_matches("*", 'W/"catalog-v1"') is True

    def test_exact_single_tag_matches(self):
        from dinary.api.catalog import _if_none_match_matches

        assert _if_none_match_matches('W/"catalog-v1"', 'W/"catalog-v1"') is True
        assert _if_none_match_matches('W/"catalog-v2"', 'W/"catalog-v1"') is False

    def test_comma_separated_list_member_matches(self):
        from dinary.api.catalog import _if_none_match_matches

        header = 'W/"catalog-v0", W/"catalog-v1", W/"catalog-v2"'
        assert _if_none_match_matches(header, 'W/"catalog-v1"') is True

    def test_comma_separated_list_without_match(self):
        from dinary.api.catalog import _if_none_match_matches

        header = 'W/"catalog-v0", W/"catalog-v2"'
        assert _if_none_match_matches(header, 'W/"catalog-v1"') is False

    def test_whitespace_around_tags_is_tolerated(self):
        from dinary.api.catalog import _if_none_match_matches

        header = '   W/"catalog-v1"   ,   W/"catalog-v2"   '
        assert _if_none_match_matches(header, 'W/"catalog-v2"') is True
