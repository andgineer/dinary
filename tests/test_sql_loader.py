"""Tests for SQL file loader and typed row mapper."""

import dataclasses

import allure
import duckdb
import pytest

from dinary.services.sql_loader import _cache, fetchall_as, fetchone_as, load_sql

ALL_SQL_FILES = [
    "forward_projection.sql",
    "get_existing_expense.sql",
    "get_month_expenses.sql",
    "insert_expense.sql",
    "list_categories.sql",
    "resolve_mapping.sql",
    "resolve_mapping_for_year.sql",
    "seed_load_categories.sql",
]


@pytest.fixture(autouse=True)
def _clear_cache():
    _cache.clear()
    yield
    _cache.clear()


@dataclasses.dataclass(slots=True)
class _PairRow:
    x: int
    y: str


@dataclasses.dataclass(slots=True)
class _WrongRow:
    a: int
    b: str


@allure.epic("SQL Loader")
@allure.feature("File Loading")
class TestLoadSql:
    @pytest.mark.parametrize("name", ALL_SQL_FILES)
    def test_all_sql_files_loadable(self, name):
        text = load_sql(name)
        assert len(text) > 0
        assert "SELECT" in text.upper() or "INSERT" in text.upper()

    def test_caches_after_first_load(self):
        load_sql("resolve_mapping.sql")
        assert "resolve_mapping.sql" in _cache

        _cache["resolve_mapping.sql"] = "-- cached"
        assert load_sql("resolve_mapping.sql") == "-- cached"

    def test_missing_file_raises(self):
        with pytest.raises(Exception):
            load_sql("nonexistent.sql")


@allure.epic("SQL Loader")
@allure.feature("Row Mapping")
class TestRowMapper:
    def test_fetchone_as_maps_columns(self):
        con = duckdb.connect(":memory:")
        result = fetchone_as(_PairRow, con, "SELECT 42 AS x, 'hello' AS y")
        con.close()

        assert result is not None
        assert result.x == 42
        assert result.y == "hello"

    def test_fetchone_as_returns_none_on_empty(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE t (x INT)")
        result = fetchone_as(_PairRow, con, "SELECT x, '' AS y FROM t")
        con.close()

        assert result is None

    def test_fetchone_as_validates_columns_on_empty(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE t (x INT)")
        with pytest.raises(RuntimeError, match="SQL/dataclass mismatch"):
            fetchone_as(_PairRow, con, "SELECT x FROM t")
        con.close()

    def test_fetchall_as_maps_multiple(self):
        con = duckdb.connect(":memory:")
        rows = fetchall_as(_PairRow, con, "SELECT 1 AS x, 'a' AS y UNION ALL SELECT 2, 'b'")
        con.close()

        assert len(rows) == 2
        assert rows[0].x == 1
        assert rows[1].y == "b"

    def test_fetchall_as_returns_empty_list(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE t (x INT, y TEXT)")
        rows = fetchall_as(_PairRow, con, "SELECT x, y FROM t")
        con.close()

        assert rows == []

    def test_fetchall_as_validates_columns_on_empty(self):
        con = duckdb.connect(":memory:")
        con.execute("CREATE TABLE t (x INT)")
        with pytest.raises(RuntimeError, match="SQL/dataclass mismatch"):
            fetchall_as(_PairRow, con, "SELECT x FROM t")
        con.close()

    def test_column_mismatch_raises(self):
        con = duckdb.connect(":memory:")
        with pytest.raises(RuntimeError, match="SQL/dataclass mismatch"):
            fetchone_as(_WrongRow, con, "SELECT 42 AS x, 'hello' AS y")
        con.close()

    def test_extra_column_raises(self):
        con = duckdb.connect(":memory:")
        with pytest.raises(RuntimeError, match="extra"):
            fetchone_as(_PairRow, con, "SELECT 1 AS x, 'a' AS y, 99 AS z")
        con.close()

    def test_missing_column_raises(self):
        con = duckdb.connect(":memory:")
        with pytest.raises(RuntimeError, match="missing"):
            fetchone_as(_PairRow, con, "SELECT 1 AS x")
        con.close()
