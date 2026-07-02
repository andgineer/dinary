"""Versioned schema migrations for the server's SQLite ledger via yoyo."""

import contextlib
import typing as t
from collections import abc
from functools import cache
from pathlib import Path

from yoyo import read_migrations, utils
from yoyo.backends.base import DatabaseBackend
from yoyo.connections import DatabaseURI
from yoyo.migrations import default_migration_table


class SQLiteBackend(DatabaseBackend):
    """Doesn't route through ``dinary.db.storage.connect`` because yoyo owns the
    transaction/savepoint lifecycle; both paths agree on isolation_level=None, FKs on,
    and WAL mode. ``Decimal``/``date``/``datetime`` values round-trip correctly only
    because importing ``storage`` elsewhere registers the type adapters as a side effect."""

    driver_module = "sqlite3"
    list_tables_sql = "SELECT name FROM sqlite_master WHERE type = 'table'"

    def connect(self, dburi: DatabaseURI):
        con = self.driver.connect(dburi.database, isolation_level=None)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        # Matches storage.connect's default so a stray operator tool holding the
        # write lock doesn't make BEGIN IMMEDIATE raise SQLITE_BUSY immediately.
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def begin(self):
        self._in_transaction = True
        # Migrations are always writers (DDL/DML), so take the RESERVED
        # lock up front: this makes ``busy_timeout`` apply at BEGIN and
        # rules out mid-migration SQLITE_BUSY at COMMIT time.
        self.connection.execute("BEGIN IMMEDIATE")

    def commit(self):
        self.connection.execute("COMMIT")
        self._in_transaction = False

    def rollback(self):
        with contextlib.suppress(self.DatabaseError):
            self.connection.execute("ROLLBACK")
        self._in_transaction = False

    def savepoint(self, id):  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-override]
        self.connection.execute(f"SAVEPOINT {id}")

    def savepoint_release(self, id):  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-override]
        self.connection.execute(f"RELEASE SAVEPOINT {id}")

    def savepoint_rollback(self, id):  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-override]
        self.connection.execute(f"ROLLBACK TO SAVEPOINT {id}")

    def execute(self, sql, params: abc.Mapping[str, t.Any] | None = None):
        sql, queryparams = utils.change_param_style(
            self.driver.paramstyle,
            sql,
            params,
        )
        cursor = self.connection.cursor()
        cursor.execute(sql, queryparams or [])
        return cursor


@cache
def _migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


@cache
def _read_migrations():
    return read_migrations(str(_migrations_dir()))


def _backend_for(path: Path) -> SQLiteBackend:
    backend = SQLiteBackend(
        DatabaseURI(
            scheme="sqlite",
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


def migration_ids() -> list[str]:
    """Return sorted migration IDs from the migrations directory."""
    return sorted(m.id for m in _read_migrations())


def migrate_db(path: Path) -> None:
    """Apply all pending migrations for the given SQLite file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    migrations = _read_migrations()
    with _backend_for(path) as backend, backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
