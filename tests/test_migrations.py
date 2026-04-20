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
                "import_mapping",
                "import_mapping_tags",
                "import_sources",
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
            assert {"expenses", "expense_tags", "sheet_logging_jobs", "income"}.issubset(tables)
            assert "sheet_sync_jobs" not in tables, (
                "sheet_sync_jobs must be renamed to sheet_logging_jobs by migration 0002"
            )
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

    def test_sheet_logging_jobs_keyed_by_expense_id(self, tmp_path: Path):
        db_path = tmp_path / "budget_2026.duckdb"
        db_migrations.migrate_budget_db(db_path)
        con = duckdb.connect(str(db_path))
        try:
            cols = {r[0] for r in con.execute("DESCRIBE sheet_logging_jobs").fetchall()}
            assert {"expense_id", "status", "claim_token", "claimed_at"}.issubset(cols)
            for legacy in ("year", "month"):
                assert legacy not in cols
        finally:
            con.close()


@allure.epic("Migrations")
@allure.feature("Config DB (0002 logging_mapping + import rename)")
class TestLoggingMappingMigration:
    def test_logging_mapping_tables_created(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "logging_mapping" in tables
            assert "logging_mapping_tags" in tables
        finally:
            con.close()

    def test_logging_mapping_schema(self, tmp_path: Path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            cols = {r[0] for r in con.execute("DESCRIBE logging_mapping").fetchall()}
            assert {"id", "category_id", "event_id", "sheet_category", "sheet_group"}.issubset(
                cols,
            )

            tag_cols = {r[0] for r in con.execute("DESCRIBE logging_mapping_tags").fetchall()}
            assert {"mapping_id", "tag_id"}.issubset(tag_cols)
        finally:
            con.close()

    def test_bootstrap_from_year_zero_on_upgrade(self, tmp_path: Path):
        """Simulate an upgrade: apply migration 0001 only via yoyo, insert
        year=0 rows into the legacy ``sheet_mapping``, then let the rest of
        the migrations run. Migration 0002 must mirror the year=0 rows into
        ``logging_mapping``.
        """
        db_path = tmp_path / "config.duckdb"
        all_migrations = db_migrations._read("config")
        first = all_migrations[:1]

        backend = db_migrations._backend_for(db_path)
        with backend.lock():
            backend.apply_migrations(backend.to_apply(first))

        con = duckdb.connect(str(db_path))
        try:
            con.execute("INSERT INTO category_groups VALUES (1, 'g', 1)")
            con.execute("INSERT INTO categories VALUES (1, 'food', 1)")
            con.execute("INSERT INTO tags VALUES (1, 'dog')")
            con.execute(
                "INSERT INTO sheet_import_sources"
                " (year, spreadsheet_id, worksheet_name, layout_key, notes)"
                " VALUES (2025, 'ssid', 'Sheet1', 'default', NULL)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
                " category_id, event_id) VALUES (1, 0, 'Food', 'Dog', 1, NULL)",
            )
            con.execute(
                "INSERT INTO sheet_mapping (id, year, sheet_category, sheet_group,"
                " category_id, event_id) VALUES (2, 2025, 'Food', 'DogYearly', 1, NULL)",
            )
            con.execute("INSERT INTO sheet_mapping_tags VALUES (1, 1)")
        finally:
            con.close()

        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            rows = con.execute(
                "SELECT category_id, sheet_category, sheet_group FROM logging_mapping ORDER BY id",
            ).fetchall()
            assert rows == [(1, "Food", "Dog")], (
                f"logging_mapping must contain only year=0 rows from sheet_mapping, got {rows}"
            )
            tag_rows = con.execute(
                "SELECT mapping_id, tag_id FROM logging_mapping_tags ORDER BY mapping_id",
            ).fetchall()
            assert tag_rows == [(1, 1)]
        finally:
            con.close()

    def test_empty_import_mapping_no_error(self, tmp_path: Path):
        """Bootstrap runs without error when import_mapping is empty."""
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            count = con.execute("SELECT COUNT(*) FROM logging_mapping").fetchone()[0]
            assert count == 0
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
