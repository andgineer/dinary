import os
import sqlite3

import allure
import duckdb
import pytest

import dinary_analytics.connection as connection_module
from dinary_analytics.connection import (
    LEDGER_TABLES,
    accounting_currency,
    build_ledger_cache,
    ledger_schema,
    open_ledger,
    open_ledger_restricted,
)
from dinary_analytics.paths import LEDGER_CACHE_FILENAME

SECRET_MARKER = "sk-secret-marker-7f3a"


@pytest.fixture
def ledger_sqlite(tmp_path):
    db = tmp_path / "dinary.db"
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datetime TEXT NOT NULL,
            amount REAL NOT NULL,
            amount_original REAL NOT NULL,
            currency_original TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            event_id INTEGER,
            comment TEXT,
            sheet_category TEXT,
            sheet_group TEXT
        );
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            group_id INTEGER,
            is_active INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO categories VALUES (1, 'Groceries', 1, 1);
        INSERT INTO expenses
            VALUES (1, '2024-03-15 10:00:00', 45.0, 4950.0, 'RSD', 1, NULL, 'supermarket', NULL, NULL);
        INSERT INTO expenses
            VALUES (2, '2024-03-20 14:00:00', 10.0, 1100.0, 'RSD', 1, NULL, 'coffee', NULL, NULL);
    """)
    con.commit()
    con.close()
    return db


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_open_ledger_attaches_sqlite(ledger_sqlite):
    con = open_ledger(replica_path=ledger_sqlite)
    try:
        count = con.execute("SELECT COUNT(*) FROM ledger.expenses").fetchone()[0]
        assert count == 2
    finally:
        con.close()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_open_ledger_is_read_only(ledger_sqlite):
    con = open_ledger(replica_path=ledger_sqlite)
    try:
        with pytest.raises(Exception):
            con.execute(
                "INSERT INTO ledger.expenses "
                "VALUES (99, '2024-01-01', 1.0, 100.0, 'RSD', 1, NULL, NULL, NULL, NULL)"
            )
    finally:
        con.close()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_open_ledger_can_query_join(ledger_sqlite):
    con = open_ledger(replica_path=ledger_sqlite)
    try:
        rows = con.execute(
            "SELECT e.amount, c.name FROM ledger.expenses e "
            "JOIN ledger.categories c ON e.category_id = c.id"
        ).fetchall()
        assert len(rows) == 2
        assert all(r[1] == "Groceries" for r in rows)
    finally:
        con.close()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_schema_is_read_from_the_replica(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.executescript("""
        ALTER TABLE expenses ADD COLUMN store_id INTEGER;
        CREATE TABLE llmbroker_secrets (ref TEXT, value TEXT);
    """)
    con.commit()
    con.close()

    schema = ledger_schema(replica_path=ledger_sqlite)

    assert "store_id" in schema
    assert "CREATE TABLE categories" in schema
    assert f"-- expenses: {LEDGER_TABLES['expenses']}" in schema
    assert schema.index("CREATE TABLE expenses") < schema.index("CREATE TABLE categories")
    assert "llmbroker_secrets" not in schema
    assert "CREATE TABLE income" not in schema


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_schema_handles_a_path_with_spaces(ledger_sqlite, tmp_path):
    spaced = tmp_path / "Application Support"
    spaced.mkdir()
    target = spaced / "replica.db"
    target.write_bytes(ledger_sqlite.read_bytes())

    assert "CREATE TABLE expenses" in ledger_schema(replica_path=target)


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_accounting_currency_comes_from_the_replica(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.executescript("""
        CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO app_metadata VALUES ('accounting_currency', 'RSD');
    """)
    con.commit()
    con.close()

    assert accounting_currency(replica_path=ledger_sqlite) == "RSD"


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_accounting_currency_missing_table_is_reported(ledger_sqlite):
    with pytest.raises(LookupError, match="app_metadata"):
        accounting_currency(replica_path=ledger_sqlite)


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_accounting_currency_missing_row_is_reported(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.execute("CREATE TABLE app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.commit()
    con.close()

    with pytest.raises(LookupError, match="accounting currency"):
        accounting_currency(replica_path=ledger_sqlite)


@pytest.fixture
def replica_with_secrets(ledger_sqlite):
    con = sqlite3.connect(str(ledger_sqlite))
    con.executescript(f"""
        CREATE TABLE llmbroker_secrets (ref TEXT, value TEXT);
        INSERT INTO llmbroker_secrets VALUES ('openai', '{SECRET_MARKER}');
    """)
    con.commit()
    con.close()
    return ledger_sqlite


def _cache_of(replica):
    return replica.with_name(LEDGER_CACHE_FILENAME)


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_restricted_connection_reads_the_ledger(replica_with_secrets):
    con = open_ledger_restricted(replica_path=replica_with_secrets)
    try:
        rows = con.execute(
            "SELECT e.amount, c.name FROM ledger.expenses e "
            "JOIN ledger.categories c ON e.category_id = c.id ORDER BY e.id"
        ).fetchall()
    finally:
        con.close()
    assert rows == [(45.0, "Groceries"), (10.0, "Groceries")]


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_restricted_connection_keeps_the_replica_column_types(tmp_path):
    replica = tmp_path / "typed.db"
    con = sqlite3.connect(str(replica))
    con.executescript("""
        CREATE TABLE expenses (id INTEGER PRIMARY KEY, datetime TIMESTAMP NOT NULL, amount REAL);
        INSERT INTO expenses VALUES (1, '2024-03-15 10:00:00', 45.0);
    """)
    con.commit()
    con.close()

    trusted = open_ledger(replica_path=replica)
    restricted = open_ledger_restricted(replica_path=replica)
    try:
        expected = trusted.execute("DESCRIBE ledger.expenses").fetchall()
        actual = restricted.execute("DESCRIBE ledger.expenses").fetchall()
    finally:
        trusted.close()
        restricted.close()
    assert [(c[0], c[1]) for c in actual] == [(c[0], c[1]) for c in expected]
    assert ("datetime", "TIMESTAMP") in [(c[0], c[1]) for c in actual]


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM ledger.llmbroker_secrets",
        "ATTACH '{replica}' AS again (TYPE sqlite)",
        "ATTACH '{replica}' AS again",
        "SELECT * FROM sqlite_scan('{replica}', 'llmbroker_secrets')",
        "SELECT * FROM read_text('{replica}')",
        "SELECT * FROM read_blob('{replica}')",
        "LOAD sqlite",
        "INSTALL sqlite",
        "SET enable_external_access = true",
        "SET autoload_known_extensions = true",
        "COPY ledger.expenses TO '{tmp}/out.csv'",
        "COPY ledger.expenses TO '{cache}.tmp/out.csv'",
        "COPY (SELECT 'junk' AS x) TO '{cache}.wal' (FORMAT csv)",
        "COPY (SELECT 'junk' AS x) TO '{cache}.wal.recovery' (FORMAT csv)",
        "COPY (SELECT 'junk' AS x) TO '{cache}' (FORMAT csv)",
        "ATTACH '{cache}' AS again (READ_ONLY)",
    ],
)
def test_restricted_connection_refuses_everything_else(replica_with_secrets, tmp_path, sql):
    statement = sql.format(
        replica=replica_with_secrets,
        tmp=tmp_path,
        cache=_cache_of(replica_with_secrets),
    )
    _cache_of(replica_with_secrets).with_suffix(".duckdb.tmp").mkdir()
    con = open_ledger_restricted(replica_path=replica_with_secrets)
    try:
        with pytest.raises(duckdb.Error) as exc_info:
            con.execute(statement).fetchall()
    finally:
        con.close()
    assert SECRET_MARKER not in str(exc_info.value)
    assert not (tmp_path / "out.csv").exists()
    cache = _cache_of(replica_with_secrets)
    assert not any(cache.with_suffix(".duckdb.tmp").iterdir())
    assert not cache.with_name(f"{cache.name}.wal").exists()
    assert not cache.with_name(f"{cache.name}.wal.recovery").exists()


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_restricted_connection_lists_only_ledger_tables(replica_with_secrets):
    con = open_ledger_restricted(replica_path=replica_with_secrets)
    try:
        tables = con.execute("SELECT schema_name, table_name FROM duckdb_tables()").fetchall()
    finally:
        con.close()
    assert sorted(tables) == [("ledger", "categories"), ("ledger", "expenses")]
    assert set(t for _, t in tables) <= set(LEDGER_TABLES)


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_cache_holds_no_other_table(replica_with_secrets, tmp_path):
    cache = tmp_path / "cache.duckdb"
    build_ledger_cache(replica_with_secrets, cache)

    assert SECRET_MARKER.encode() not in cache.read_bytes()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("cache.duckdb")] == [
        "cache.duckdb",
    ]


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_restricted_connection_allows_no_paths(replica_with_secrets):
    con = open_ledger_restricted(replica_path=replica_with_secrets)
    try:
        settings = dict(
            con.execute(
                "SELECT name, value FROM duckdb_settings() "
                "WHERE name IN ('allowed_paths', 'allowed_directories', 'enable_external_access')",
            ).fetchall(),
        )
    finally:
        con.close()
    assert settings == {
        "allowed_paths": "[]",
        "allowed_directories": "[]",
        "enable_external_access": "false",
    }


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_restricted_connections_can_be_open_at_once(replica_with_secrets):
    first = open_ledger_restricted(replica_path=replica_with_secrets)
    second = open_ledger_restricted(replica_path=replica_with_secrets)
    try:
        counts = [
            con.execute("SELECT count(*) FROM ledger.expenses").fetchone()[0]
            for con in (first, second)
        ]
    finally:
        first.close()
        second.close()
    assert counts == [2, 2]


def _add_expense(replica, expense_id):
    con = sqlite3.connect(str(replica))
    con.execute(
        "INSERT INTO expenses VALUES (?, '2024-04-01', 7.0, 770.0, 'RSD', 1, NULL, NULL, NULL, NULL)",
        [expense_id],
    )
    con.commit()
    con.close()


def _expense_count(replica):
    con = open_ledger_restricted(replica_path=replica)
    try:
        return con.execute("SELECT count(*) FROM ledger.expenses").fetchone()[0]
    finally:
        con.close()


@pytest.fixture
def cache_builds(monkeypatch):
    builds = []
    real_build = connection_module.build_ledger_cache

    def counting_build(replica_path, cache_path):
        builds.append(replica_path)
        real_build(replica_path, cache_path)

    monkeypatch.setattr(connection_module, "build_ledger_cache", counting_build)
    return builds


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_cache_is_reused_while_the_replica_is_unchanged(replica_with_secrets, cache_builds):
    assert _expense_count(replica_with_secrets) == 2
    assert _expense_count(replica_with_secrets) == 2

    assert len(cache_builds) == 1


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_cache_is_rebuilt_after_the_replica_changes(replica_with_secrets, cache_builds):
    assert _expense_count(replica_with_secrets) == 2
    _add_expense(replica_with_secrets, 3)
    later = replica_with_secrets.stat().st_mtime_ns + 1_000_000_000
    os.utime(replica_with_secrets, ns=(later, later))

    assert _expense_count(replica_with_secrets) == 3
    assert len(cache_builds) == 2


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_cache_is_rebuilt_for_a_replica_with_an_older_mtime(replica_with_secrets):
    assert _expense_count(replica_with_secrets) == 2
    _add_expense(replica_with_secrets, 3)
    earlier = replica_with_secrets.stat().st_mtime_ns - 86_400_000_000_000
    os.utime(replica_with_secrets, ns=(earlier, earlier))

    assert _expense_count(replica_with_secrets) == 3


@allure.epic("Analytics")
@allure.feature("Ledger Connection")
def test_ledger_cache_built_during_a_replica_swap_is_rebuilt(replica_with_secrets, monkeypatch):
    real_replace = os.replace
    swapped = []
    built_from = replica_with_secrets.stat().st_mtime_ns

    def replace_after_replica_swap(src, dst):
        if not swapped:
            swapped.append(True)
            _add_expense(replica_with_secrets, 3)
            # The new replica is older than the finished cache file, as in a real swap mid-build.
            os.utime(replica_with_secrets, ns=(built_from + 1, built_from + 1))
        real_replace(src, dst)

    monkeypatch.setattr(connection_module.os, "replace", replace_after_replica_swap)

    assert _expense_count(replica_with_secrets) == 2
    assert _expense_count(replica_with_secrets) == 3
