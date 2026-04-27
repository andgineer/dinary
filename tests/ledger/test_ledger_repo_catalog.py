"""Catalog-side ledger_repo tests: connection lifecycle,
``catalog_version``, ``list_categories``, sheet-mapping 3D
resolution, and ``get_category_name``.

Per-test DB lives under ``tmp_path`` via the autouse ``_tmp_data_dir``
fixture imported from ``_ledger_repo_helpers``.

The drain-worker-facing ``logging_projection`` helper has its own
sibling module :file:`test_ledger_repo_logging_projection.py`.
"""

import allure
import pytest

from dinary.services import ledger_repo

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    _tmp_data_dir,
    fresh_db,
    populated_catalog,
)


@allure.epic("Ledger repo")
@allure.feature("Connection lifecycle")
class TestConnectionLifecycle:
    def test_init_creates_file(self, tmp_path):
        assert not ledger_repo.DB_PATH.exists()
        ledger_repo.init_db()
        assert ledger_repo.DB_PATH.exists()

    def test_get_connection_before_init_autocreates(self, tmp_path):
        """``get_connection`` opens the file even without explicit init."""
        con = ledger_repo.get_connection()
        try:
            row = con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        assert row == (1,)

    def test_close_releases_singleton(self, fresh_db):
        # ``close_connection`` is a post-SQLite-port no-op retained for
        # test-harness compatibility; ``get_connection`` always returns
        # a fresh connection after the port. The test still exercises
        # the call to pin the no-op contract.
        ledger_repo.close_connection()
        con = ledger_repo.get_connection()
        try:
            con.execute("SELECT 1").fetchone()
        finally:
            con.close()

    def test_multiple_connections_see_each_others_commits(self, fresh_db):
        """Two independent connections observe each other's commits.

        After the SQLite port ``get_connection`` hands out one fresh
        connection per call (no shared engine). WAL mode plus an
        explicit commit on the writer lets the second connection
        observe the new row.
        """
        c1 = ledger_repo.get_connection()
        c2 = ledger_repo.get_connection()
        try:
            c1.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (42, 'g42', 99)",
            )
            c1.commit()
            row = c2.execute(
                "SELECT name FROM category_groups WHERE id = 42",
            ).fetchone()
        finally:
            c1.close()
            c2.close()
        assert row == ("g42",)


@allure.epic("Ledger repo")
@allure.feature("Catalog version")
class TestCatalogVersion:
    def test_initial_version_is_one(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_catalog_version(con) == 1
        finally:
            con.close()

    def test_set_then_get(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            ledger_repo.set_catalog_version(con, 42)
            assert ledger_repo.get_catalog_version(con) == 42
        finally:
            con.close()

    def test_missing_key_raises(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute("DELETE FROM app_metadata WHERE key = 'catalog_version'")
            with pytest.raises(RuntimeError, match="catalog_version"):
                ledger_repo.get_catalog_version(con)
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("List categories (is_active)")
class TestListCategories:
    def test_filters_inactive_rows(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'Food', 1, 1), (2, 'Gone', 2, 0)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'active', 1, 1),"
                " (2, 'retired', 1, 0),"
                " (3, 'orphan-group', 2, 1)",
            )
            rows = ledger_repo.list_categories(con)
        finally:
            con.close()
        names = {r.name for r in rows}
        # 'retired' filtered out (is_active=false on category); 'orphan-group'
        # filtered out (group is_active=false).
        assert names == {"active"}

    def test_ordering(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order, is_active)"
                " VALUES (1, 'Z', 2, 1), (2, 'A', 1, 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id, is_active)"
                " VALUES (1, 'b', 1, 1),"
                " (2, 'a', 1, 1),"
                " (3, 'c', 2, 1)",
            )
            rows = ledger_repo.list_categories(con)
        finally:
            con.close()
        # Groups ordered by sort_order: 'A' (1) before 'Z' (2); within each
        # group, categories ordered by name ascending.
        assert [(r.name, r.group_name) for r in rows] == [
            ("c", "A"),
            ("a", "Z"),
            ("b", "Z"),
        ]


@allure.epic("Ledger repo")
@allure.feature("Sheet mapping (3D)")
class TestSheetMapping:
    def test_resolve_year_zero_default(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            row = ledger_repo.resolve_mapping(con, "еда", "собака")
            assert row is not None
            assert row.category_id == 1
            assert row.event_id is None
        finally:
            con.close()

    def test_year_specific_overrides_default(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            row = ledger_repo.resolve_mapping_for_year(con, "еда", "собака", 2026)
            assert row is not None
            assert row.category_id == 2
            assert row.event_id == 10
        finally:
            con.close()

    def test_year_falls_back_to_zero(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            row = ledger_repo.resolve_mapping_for_year(con, "еда", "собака", 2024)
            assert row is not None
            assert row.category_id == 1
        finally:
            con.close()

    def test_unknown_returns_none(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.resolve_mapping(con, "missing", "?") is None
        finally:
            con.close()

    def test_get_mapping_tag_ids(self, populated_catalog):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_mapping_tag_ids(con, 1) == [1]
            assert ledger_repo.get_mapping_tag_ids(con, 2) == []
        finally:
            con.close()


@allure.epic("Ledger repo")
@allure.feature("get_category_name")
class TestGetCategoryName:
    def test_existing(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'еда', 1)",
            )
            con.commit()
            assert ledger_repo.get_category_name(con, 1) == "еда"
        finally:
            con.close()

    def test_missing(self, fresh_db):
        con = ledger_repo.get_connection()
        try:
            assert ledger_repo.get_category_name(con, 999) is None
        finally:
            con.close()
