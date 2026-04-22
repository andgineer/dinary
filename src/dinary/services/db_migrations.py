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
    """Minimal yoyo backend for local SQLite files.

    Keeps the dependency surface small: we only need ``apply_migrations`` +
    ``to_apply`` with a single-file connection. The built-in yoyo SQLite
    backend does more than we need (authentication helpers, alt drivers);
    inlining the minimum keeps the build boring and makes the PRAGMA
    configuration explicit.

    ``PRAGMA foreign_keys=ON`` is applied on every connect so referential
    integrity is enforced during migration DDL just like at runtime.
    ``PRAGMA journal_mode=WAL`` is applied once on the DB file (it is
    persisted across reopens by SQLite) so later runtime connections
    also land in WAL mode; the Litestream sidecar requires that.

    Note on connection paths: this backend deliberately does **not**
    route through ``dinary.services.sqlite_types.connect`` because
    yoyo manages the lifecycle (commit/rollback, savepoint dance)
    and expects to own ``isolation_level`` and PRAGMA setup. Both
    paths agree on the three invariants that matter for correctness
    — ``isolation_level=None`` (autocommit + explicit BEGIN), FKs
    on, WAL mode. The type-adapter registration in ``sqlite_types``
    is a module-level side effect of importing it, so any
    ``Decimal`` / ``date`` / ``datetime`` / ``BOOLEAN`` values that
    happen to cross this backend's cursors still round-trip
    correctly as long as ``sqlite_types`` has been imported at
    least once in the process (which is true at runtime because
    ``ledger_repo`` imports it eagerly, and true in tests because
    ``tests/conftest.py`` does the same). If a future migration
    starts binding those Python types via ``?`` parameters, keep
    this contract in mind or switch the backend over to
    ``sqlite_types.connect``.
    """

    driver_module = "sqlite3"
    list_tables_sql = "SELECT name FROM sqlite_master WHERE type = 'table'"

    def connect(self, dburi: DatabaseURI):
        con = self.driver.connect(dburi.database, isolation_level=None)
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        # Match the 5-second window every other connection carries
        # (``sqlite_types.connect``'s default). Migrations normally
        # run with the service stopped so contention is minimal, but
        # if a stray operator tool still holds the write lock, the
        # ``BEGIN IMMEDIATE`` below would otherwise raise
        # ``SQLITE_BUSY`` immediately instead of waiting out the
        # timeout.
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
    return Path(__file__).resolve().parent.parent / "migrations"


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


def migrate_db(path: Path) -> None:
    """Apply all pending migrations for the given SQLite file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    migrations = _read_migrations()
    with _backend_for(path) as backend, backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
