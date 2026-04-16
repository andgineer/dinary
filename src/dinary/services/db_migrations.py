"""Versioned DuckDB schema migrations via yoyo."""

from __future__ import annotations

import contextlib
import typing as t
from collections import abc
from functools import cache
from pathlib import Path

from yoyo import read_migrations, utils
from yoyo.backends.base import DatabaseBackend
from yoyo.connections import DatabaseURI
from yoyo.migrations import default_migration_table


class DuckDBBackend(DatabaseBackend):
    """Minimal yoyo backend for local DuckDB files.

    DuckDB binds transactions to cursors, so all SQL must flow through
    ``connection.execute()`` instead of creating fresh cursors.
    DuckDB also lacks SAVEPOINT support, so nested-transaction methods
    are no-ops.
    """

    driver_module = "duckdb"
    list_tables_sql = (
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
    )

    def connect(self, dburi: DatabaseURI):
        return self.driver.connect(dburi.database)

    def begin(self):
        self._in_transaction = True
        self.connection.execute("BEGIN TRANSACTION")

    def commit(self):
        self.connection.execute("COMMIT")
        self._in_transaction = False

    def rollback(self):
        with contextlib.suppress(self.DatabaseError):
            self.connection.execute("ROLLBACK")
        self._in_transaction = False

    def savepoint(self, id):  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-override]
        pass

    def savepoint_release(self, id):  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-override]
        pass

    def savepoint_rollback(self, id):  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-override]
        pass

    def execute(self, sql, params: abc.Mapping[str, t.Any] | None = None):
        sql, queryparams = utils.change_param_style(
            self.driver.paramstyle,
            sql,
            params,
        )
        self.connection.execute(sql, queryparams or [])
        return self.connection


@cache
def _migration_dir(kind: str) -> Path:
    root = Path(__file__).resolve().parent.parent / "migrations"
    return root / kind


@cache
def _read(kind: str):
    return read_migrations(str(_migration_dir(kind)))


def _backend_for(path: Path) -> DuckDBBackend:
    backend = DuckDBBackend(
        DatabaseURI(
            scheme="duckdb",
            username=None,
            password=None,
            hostname=None,
            port=None,
            database=str(path),
            args={},
        ),
        default_migration_table,
    )
    backend.init_database()
    return backend


def migrate_path(path: Path, kind: str) -> None:
    """Apply all pending migrations for the given DuckDB file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    migrations = _read(kind)
    with _backend_for(path) as backend, backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))


def migrate_config_db(path: Path) -> None:
    migrate_path(path, "config")


def migrate_budget_db(path: Path) -> None:
    migrate_path(path, "budget")
