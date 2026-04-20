"""Tests for the unified DuckDB migration in ``db_migrations.migrate_db``.

After the single-DB refactor there is only one migration target:
``data/dinary.duckdb``. These tests verify that applying the bundled
migrations to a fresh file produces the expected schema and seed rows.
"""

import allure
import duckdb
import pytest

from dinary.services import db_migrations, duckdb_repo


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Point ``duckdb_repo`` at an empty tmp file and apply all migrations."""
    monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
    monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")
    db_migrations.migrate_db(duckdb_repo.DB_PATH)
    return duckdb_repo.DB_PATH


def _table_names(con: duckdb.DuckDBPyConnection) -> set[str]:
    rows = con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()",
    ).fetchall()
    return {r[0] for r in rows}


def _column_names(con: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = ? AND table_schema = current_schema()",
        [table],
    ).fetchall()
    return {r[0] for r in rows}


@allure.epic("Migrations")
@allure.feature("Initial schema")
class TestInitialSchema:
    def test_creates_expected_catalog_tables(self, fresh_db):
        con = duckdb.connect(str(fresh_db))
        try:
            tables = _table_names(con)
        finally:
            con.close()

        expected = {
            "category_groups",
            "categories",
            "events",
            "tags",
            "exchange_rates",
            "import_sources",
            "import_mapping",
            "import_mapping_tags",
            "logging_mapping",
            "logging_mapping_tags",
            "app_metadata",
        }
        assert expected.issubset(tables)

    def test_creates_expected_ledger_tables(self, fresh_db):
        con = duckdb.connect(str(fresh_db))
        try:
            tables = _table_names(con)
        finally:
            con.close()

        assert {"expenses", "expense_tags", "sheet_logging_jobs", "income"}.issubset(tables)

    def test_no_old_config_or_budget_tables(self, fresh_db):
        """The old split-DB refactor dropped these legacy artefacts."""
        con = duckdb.connect(str(fresh_db))
        try:
            tables = _table_names(con)
        finally:
            con.close()

        assert "expense_id_registry" not in tables

    def test_catalog_tables_have_is_active_column(self, fresh_db):
        con = duckdb.connect(str(fresh_db))
        try:
            for table in ("category_groups", "categories", "events", "tags"):
                assert "is_active" in _column_names(con, table), table
        finally:
            con.close()

    def test_app_metadata_is_key_value(self, fresh_db):
        con = duckdb.connect(str(fresh_db))
        try:
            cols = _column_names(con, "app_metadata")
            row = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'catalog_version'",
            ).fetchone()
        finally:
            con.close()
        assert cols == {"key", "value"}
        assert row is not None
        assert row[0] == "1"

    def test_expenses_has_client_expense_id_unique(self, fresh_db):
        con = duckdb.connect(str(fresh_db))
        try:
            assert "client_expense_id" in _column_names(con, "expenses")
            con.execute(
                "INSERT INTO category_groups (id, name, sort_order) VALUES (1, 'g', 1)",
            )
            con.execute(
                "INSERT INTO categories (id, name, group_id) VALUES (1, 'c', 1)",
            )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ["cid-1", "2026-04-15 12:00:00", 100, 100, "RSD", 1],
            )
            # Re-inserting the same client_expense_id must violate UNIQUE.
            with pytest.raises(duckdb.ConstraintException):
                con.execute(
                    "INSERT INTO expenses (client_expense_id, datetime, amount,"
                    " amount_original, currency_original, category_id)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    ["cid-1", "2026-04-15 12:00:00", 100, 100, "RSD", 1],
                )
            # NULL client_expense_id is allowed many times over (bootstrap rows).
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (NULL, ?, ?, ?, ?, ?)",
                ["2026-04-15 12:00:00", 100, 100, "RSD", 1],
            )
            con.execute(
                "INSERT INTO expenses (client_expense_id, datetime, amount,"
                " amount_original, currency_original, category_id)"
                " VALUES (NULL, ?, ?, ?, ?, ?)",
                ["2026-04-16 12:00:00", 50, 50, "RSD", 1],
            )
        finally:
            con.close()

    def test_sheet_logging_jobs_is_keyed_by_expense_id(self, fresh_db):
        con = duckdb.connect(str(fresh_db))
        try:
            cols = _column_names(con, "sheet_logging_jobs")
        finally:
            con.close()
        assert "expense_id" in cols
        assert "status" in cols
        assert "claim_token" in cols

    def test_idempotent_reapply(self, fresh_db):
        """Running migrate_db twice is a no-op (yoyo records applied migrations)."""
        db_migrations.migrate_db(fresh_db)
        db_migrations.migrate_db(fresh_db)

        con = duckdb.connect(str(fresh_db))
        try:
            row = con.execute(
                "SELECT value FROM app_metadata WHERE key = 'catalog_version'",
            ).fetchone()
        finally:
            con.close()
        assert row is not None
        assert row[0] == "1"


@allure.epic("Migrations")
@allure.feature("init_db integration")
class TestInitDbIntegration:
    def test_init_db_creates_file_and_connects(self, tmp_path, monkeypatch):
        monkeypatch.setattr(duckdb_repo, "DATA_DIR", tmp_path)
        monkeypatch.setattr(duckdb_repo, "DB_PATH", tmp_path / "dinary.duckdb")

        assert not duckdb_repo.DB_PATH.exists()
        duckdb_repo.init_db()
        assert duckdb_repo.DB_PATH.exists()

        con = duckdb_repo.get_connection()
        try:
            version = duckdb_repo.get_catalog_version(con)
        finally:
            con.close()
        assert version == 1
