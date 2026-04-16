"""Tests for the yoyo-based DuckDB migration layer."""

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
@allure.feature("Config DB")
class TestConfigMigrations:
    def test_creates_all_tables(self, tmp_path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            expected = {
                "category_groups", "categories", "family_members",
                "events", "event_members", "tags", "stores",
                "sheet_category_mapping",
            }
            assert expected.issubset(tables)
        finally:
            con.close()

    def test_idempotent(self, tmp_path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "categories" in tables
        finally:
            con.close()

    def test_creates_parent_directory(self, tmp_path):
        db_path = tmp_path / "sub" / "deep" / "config.duckdb"
        db_migrations.migrate_config_db(db_path)
        assert db_path.exists()

    def test_tracks_applied_migration(self, tmp_path):
        db_path = tmp_path / "config.duckdb"
        db_migrations.migrate_config_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "_yoyo_migration" in tables
            rows = con.execute("SELECT * FROM _yoyo_migration").fetchall()
            assert len(rows) == 1
        finally:
            con.close()


@allure.epic("Migrations")
@allure.feature("Budget DB")
class TestBudgetMigrations:
    def test_creates_budget_tables(self, tmp_path):
        db_path = tmp_path / "budget_2026.duckdb"
        db_migrations.migrate_budget_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert {"expenses", "expense_tags", "sheet_sync_jobs"}.issubset(tables)
        finally:
            con.close()

    def test_idempotent(self, tmp_path):
        db_path = tmp_path / "budget_2026.duckdb"
        db_migrations.migrate_budget_db(db_path)
        db_migrations.migrate_budget_db(db_path)

        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "expenses" in tables
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
            duckdb_repo.close_connection(con)

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
