"""Catalog-side ledger_repo tests: connection lifecycle,
``catalog_version``, sheet-mapping 3D resolution, and
``get_category_name``.

Per-test DB lives under ``tmp_path`` via the autouse ``_tmp_data_dir``
fixture imported from ``_ledger_repo_helpers``.

The drain-worker-facing ``logging_projection`` helper has its own
sibling module :file:`test_ledger_repo_logging_projection.py`.
"""

import allure
import pytest

from dinary.db import storage
from dinary.db.catalog import (
    get_catalog_version,
    get_category_name,
    get_mapping_tag_ids,
    resolve_mapping_for_year,
    set_catalog_version,
)

from _ledger_repo_helpers import (  # noqa: F401  (autouse + fixtures)
    data_dir,
    fresh_db,
    populated_catalog,
)


@allure.epic("Catalog")
@allure.feature("DB layer")
class TestConnectionLifecycle:
    def test_init_creates_file(self, tmp_path):
        assert not storage.DB_PATH.exists()
        storage.init_db()
        assert storage.DB_PATH.exists()

    def test_get_connection_before_init_autocreates(self, tmp_path):
        """``get_connection`` opens the file even without explicit init."""
        con = storage.get_connection()
        try:
            row = con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        assert row[0] == 1

    def test_get_connection_returns_usable_connection(self, fresh_db):
        con = storage.get_connection()
        try:
            row = con.execute("SELECT 1").fetchone()
        finally:
            con.close()
        assert row[0] == 1

    def test_multiple_connections_see_each_others_commits(self, fresh_db):
        """``get_connection`` hands out one fresh connection per call (no shared
        engine); WAL mode plus a commit lets a second connection see the new row."""
        c1 = storage.get_connection()
        c2 = storage.get_connection()
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
        assert row["name"] == "g42"


@allure.epic("Catalog")
@allure.feature("DB layer")
class TestCatalogVersion:
    def test_initial_version_is_one(self, fresh_db):
        con = storage.get_connection()
        try:
            assert get_catalog_version(con) == 1
        finally:
            con.close()

    def test_set_then_get(self, fresh_db):
        con = storage.get_connection()
        try:
            set_catalog_version(con, 42)
            assert get_catalog_version(con) == 42
        finally:
            con.close()

    def test_missing_key_raises(self, fresh_db):
        con = storage.get_connection()
        try:
            con.execute("DELETE FROM app_metadata WHERE key = 'catalog_version'")
            with pytest.raises(RuntimeError, match="catalog_version"):
                get_catalog_version(con)
        finally:
            con.close()


@allure.epic("Catalog")
@allure.feature("DB layer")
class TestSheetMapping:
    def test_year_specific_overrides_default(self, populated_catalog):
        con = storage.get_connection()
        try:
            row = resolve_mapping_for_year(con, "food", "dog", 2026)
            assert row is not None
            assert row.category_id == 2
            assert row.event_id == 10
        finally:
            con.close()

    def test_year_falls_back_to_zero(self, populated_catalog):
        con = storage.get_connection()
        try:
            row = resolve_mapping_for_year(con, "food", "dog", 2024)
            assert row is not None
            assert row.category_id == 1
        finally:
            con.close()

    def test_get_mapping_tag_ids(self, populated_catalog):
        con = storage.get_connection()
        try:
            assert get_mapping_tag_ids(con, 1) == [1]
            assert get_mapping_tag_ids(con, 2) == []
        finally:
            con.close()


@allure.epic("Catalog")
@allure.feature("DB layer")
class TestGetCategoryName:
    def test_existing(self, fresh_db):
        con = storage.get_connection()
        try:
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'food', 1)",
            )
            con.commit()
            assert get_category_name(con, 1) == "food"
        finally:
            con.close()

    def test_missing(self, fresh_db):
        con = storage.get_connection()
        try:
            assert get_category_name(con, 999) is None
        finally:
            con.close()
