"""Tests for the yoyo-based DuckDB migration layer (3D schema)."""

from pathlib import Path

import allure
import duckdb
import pytest

from dinary.services import db_migrations, duckdb_repo


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "CONFIG_DB", tmp_path / "config.duckdb")


@allure.epic("Migrations")
@allure.feature("Config DB (3D)")
class TestConfigMigrations:
    def test_creates_all_tables(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            expected = {
                "category_groups",
                "categories",
                "events",
                "tags",
                "sheet_mapping",
                "sheet_mapping_tags",
                "sheet_import_sources",
                "expense_id_registry",
                "exchange_rates",
                "app_metadata",
            }
            assert expected.issubset(tables), f"missing: {expected - tables}"
        finally:
            con.close()

    def test_no_legacy_4d_tables(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)
        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            for legacy in (
                "family_members",
                "spheres_of_life",
                "source_type_mapping",
                "category_taxonomies",
                "category_taxonomy_nodes",
                "category_taxonomy_membership",
                "stores",
                "income_sources",
            ):
                assert legacy not in tables
        finally:
            con.close()

    def test_idempotent(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "categories" in tables
        finally:
            con.close()

    def test_categories_link_to_groups(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            cols = {r[0] for r in con.execute("DESCRIBE categories").fetchall()}
            assert {"id", "name", "group_id"}.issubset(cols)
        finally:
            con.close()

    def test_app_metadata_has_initial_version(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)
        con = duckdb.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT id, catalog_version FROM app_metadata WHERE id = 1",
            ).fetchone()
            assert row == (1, 1)
        finally:
            con.close()


@allure.epic("Migrations")
@allure.feature("Budget DB (3D)")
class TestBudgetMigrations:
    def test_creates_budget_tables(self, tmp_path: Path):
        db_path = tmp_path / "budget_2026.duckdb"
        db_migrations.migrate_budget_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert {"expenses", "expense_tags", "sheet_sync_jobs", "income"}.issubset(tables)
        finally:
            con.close()

    def test_expenses_has_3d_columns(self, tmp_path: Path):
        db_path = tmp_path / "budget_2026.duckdb"
        db_migrations.migrate_budget_db(db_path)
        con = duckdb.connect(str(db_path))
        try:
            cols = {r[0] for r in con.execute("DESCRIBE expenses").fetchall()}
            assert {
                "id",
                "datetime",
                "amount",
                "amount_original",
                "currency_original",
                "category_id",
                "event_id",
                "comment",
                "sheet_category",
                "sheet_group",
            }.issubset(cols)
            for legacy in (
                "beneficiary_id",
                "sphere_of_life_id",
                "source",
                "source_type",
                "source_envelope",
            ):
                assert legacy not in cols
        finally:
            con.close()

    def test_sheet_sync_jobs_keyed_by_expense_id(self, tmp_path: Path):
        db_path = tmp_path / "budget_2026.duckdb"
        db_migrations.migrate_budget_db(db_path)
        con = duckdb.connect(str(db_path))
        try:
            cols = {r[0] for r in con.execute("DESCRIBE sheet_sync_jobs").fetchall()}
            assert {"expense_id", "status", "claim_token", "claimed_at"}.issubset(cols)
            for legacy in ("year", "month"):
                assert legacy not in cols
        finally:
            con.close()


@allure.epic("Migrations")
@allure.feature("Integration")
class TestInitIntegration:
    def test_init_config_db_uses_migrations(self):
        duckdb_repo.init_config_db()
        con = duckdb_repo.get_config_connection()
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "categories" in tables
            assert "_yoyo_migration" in tables
        finally:
            con.close()

    def test_init_budget_db_uses_migrations(self):
        duckdb_repo.init_config_db()
        path = duckdb_repo.init_budget_db(2026)
        assert path.exists()

        con = duckdb.connect(str(path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "expenses" in tables
            assert "_yoyo_migration" in tables
        finally:
            con.close()
