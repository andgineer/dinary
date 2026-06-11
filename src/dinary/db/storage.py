"""DB connection management, shared row types, and SQLite adapter/converter registration.

PRAGMAs applied on every connection: ``foreign_keys=ON``, ``journal_mode=WAL``,
``synchronous=NORMAL``, ``busy_timeout`` (write handles only).

``read_only=True`` requires the DB file to already exist — SQLite does not
auto-create a file in read-only mode.

Converter caveat: converters only fire for bare column references, not
aggregates. ``SUM(amount)`` returns ``str`` — coerce explicitly with
``Decimal(str(row[i]))``.
"""

import contextlib
import dataclasses
import logging
import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from dinary.config import settings
from dinary.db import currencies, db_migrations

logger = logging.getLogger(__name__)

DB_PATH = Path(settings.data_path)
DATA_DIR = DB_PATH.parent

_CLAIM_STALE_FLOOR_SEC = 600.0


# ---------------------------------------------------------------------------
# SQLite adapter/converter registration (was sqlite_types.py)
# ---------------------------------------------------------------------------


def _adapt_decimal(value: Decimal) -> str:
    return str(value)


def _adapt_date(value: date) -> str:
    return value.isoformat()


def _adapt_datetime(value: datetime) -> str:
    return value.isoformat(sep=" ")


def _convert_decimal(raw: bytes) -> Decimal:
    return Decimal(raw.decode())


def _convert_date(raw: bytes) -> date:
    return date.fromisoformat(raw.decode())


def _convert_datetime(raw: bytes) -> datetime:
    return datetime.fromisoformat(raw.decode())


def _convert_boolean(raw: bytes) -> bool:
    # SQLite stores BOOLEAN as INTEGER; sqlite3 passes the raw bytes of
    # the stored representation, which for INTEGER is the ASCII digits.
    return bool(int(raw))


sqlite3.register_adapter(Decimal, _adapt_decimal)
sqlite3.register_adapter(date, _adapt_date)
sqlite3.register_adapter(datetime, _adapt_datetime)

sqlite3.register_converter("DECIMAL", _convert_decimal)
sqlite3.register_converter("DATE", _convert_date)
sqlite3.register_converter("TIMESTAMP", _convert_datetime)
sqlite3.register_converter("BOOLEAN", _convert_boolean)


def connect(
    path: str,
    *,
    read_only: bool = False,
    timeout: float = 5.0,
) -> sqlite3.Connection:
    """Open a sqlite3 connection with the project's standard PRAGMAs.

    PRAGMAs applied: ``isolation_level=None`` (autocommit mode),
    ``detect_types`` (adapter/converter machinery), ``check_same_thread=False``,
    ``foreign_keys=ON``, ``journal_mode=WAL``, ``synchronous=NORMAL``,
    ``busy_timeout`` (write handles only).

    ``read_only=True`` opens via ``file:...?mode=ro`` URI and requires the
    file to exist. ``read_only=False`` creates the file if missing.
    """
    if read_only:
        uri = f"file:{path}?mode=ro"
        con = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            timeout=timeout,
        )
        con.execute("PRAGMA foreign_keys=ON")
        con.row_factory = sqlite3.Row
        return con
    con = sqlite3.connect(
        path,
        isolation_level=None,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
        timeout=timeout,
    )
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def default_claim_stale_timeout() -> timedelta:
    """Return the claim-stale cutoff used by the drain queue.

    Set to ``max(10 min, 2 × sheet_logging_drain_interval_sec)``.
    """
    return timedelta(
        seconds=max(
            _CLAIM_STALE_FLOOR_SEC,
            settings.sheet_logging_drain_interval_sec * 2,
        ),
    )


def best_effort_rollback(con: sqlite3.Connection, *, context: str) -> None:
    """Issue ROLLBACK without letting a failed rollback mask the original error."""
    try:
        con.execute("ROLLBACK")
    except Exception:
        logger.exception("Best-effort ROLLBACK failed (context: %s)", context)


# ---------------------------------------------------------------------------
# Row types shared by catalog, expense, and sheet-logging callers
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class MappingRow:
    id: int
    category_id: int
    event_id: int | None


@dataclasses.dataclass(slots=True)
class IdNameRow:
    id: int
    name: str


@dataclasses.dataclass(slots=True)
class VisibleCategoryRow:
    id: int
    code: str
    name: str
    group_id: int
    group_name: str
    group_sort_order: int


@dataclasses.dataclass(slots=True)
class CategorySearchRow:
    id: int
    code: str
    name: str
    is_active: bool
    is_hidden: bool


@dataclasses.dataclass(slots=True)
class LoggingProjectionCandidateRow:
    """A single ``sheet_mapping`` candidate row with its required tag set."""

    row_order: int
    category_id: int | None
    event_id: int | None
    sheet_category: str
    sheet_group: str
    tag_ids_json: str


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_db_path() -> Path:
    """Return the effective DB path (allows test monkeypatching of DB_PATH)."""
    return DB_PATH


def init_db() -> None:
    """Create or migrate data/dinary.db to the latest schema, and reconcile metadata.

    Must be called once per process before ``get_connection()``.
    """
    ensure_data_dir()
    db_migrations.migrate_db(_get_db_path())
    con = connect(str(_get_db_path()))
    try:
        _reconcile_accounting_currency(con)
        currencies.seed_default_if_empty(con, settings.app_currency)
    finally:
        con.close()


def _reconcile_accounting_currency(con: sqlite3.Connection) -> None:
    """Reconcile ``settings.accounting_currency`` with the DB anchor.

    Accounting-currency is a DB-wide invariant: every ``expenses.amount``
    and ``income.amount`` row on disk is denominated in it.

    Source-of-truth model:
    * ``DINARY_ACCOUNTING_CURRENCY`` (env var) is a first-deploy-only seed.
    * ``app_metadata.accounting_currency`` is the runtime source of truth.

    Resolution matrix:
    * Row absent + env non-empty -> seed.
    * Row absent + env empty -> ``RuntimeError``.
    * Row present + env empty -> take DB value silently.
    * Row present + env matches -> no-op.
    * Row present + env differs -> ``RuntimeError`` (typo-guard).
    """
    desired = settings.accounting_currency.strip().upper()

    row = con.execute(
        "SELECT value FROM app_metadata WHERE key = 'accounting_currency'",
    ).fetchone()

    if row is None:
        if not desired:
            msg = (
                "Fresh data/dinary.db and settings.accounting_currency is empty; "
                "refusing to seed an unknown accounting currency. Set "
                "DINARY_ACCOUNTING_CURRENCY in .deploy/.env to a valid ISO-4217 "
                "code (e.g. EUR) for the first deploy; subsequent runs can omit "
                "it and the value will be read back from app_metadata."
            )
            raise RuntimeError(msg)
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "INSERT INTO app_metadata (key, value) VALUES ('accounting_currency', ?)",
                [desired],
            )
        except BaseException:
            with contextlib.suppress(sqlite3.Error):
                con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
        settings.accounting_currency = desired
        logger.info("anchored accounting_currency=%s in app_metadata", desired)
        return

    stored = (row[0] or "").strip().upper()
    if not stored:
        msg = (
            "app_metadata.accounting_currency row exists but is empty; the "
            "DB is in an invalid state. Restore a known-good backup or set "
            "the row manually (e.g. "
            "``UPDATE app_metadata SET value='EUR' WHERE key='accounting_currency'``)."
        )
        raise RuntimeError(msg)

    if not desired or desired == stored:
        settings.accounting_currency = stored
        return

    msg = (
        f"Refusing to start: DB was initialised with "
        f"accounting_currency={stored!r} but current config has "
        f"DINARY_ACCOUNTING_CURRENCY={desired!r} (settings.accounting_currency). "
        "Mixing them would silently store new expenses/income in a different "
        "unit from the existing rows and invalidate every sum and report. "
        "Either revert the env override (.deploy/.env or the systemd unit) "
        "to match the stored value, unset it entirely (the server reads the "
        "anchored value from app_metadata when the env var is empty), or, "
        "if this is an intentional migration, convert every expenses/income "
        "row to the new currency and update the app_metadata row manually "
        "before restarting."
    )
    raise RuntimeError(msg)


def get_connection() -> sqlite3.Connection:
    """Return a fresh sqlite3 connection on ``DB_PATH``.

    Callers must close the connection (``try ... finally: con.close()``).
    """
    return connect(str(_get_db_path()))


@contextlib.contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Context manager: open a connection, close it on exit."""
    con = get_connection()
    try:
        yield con
    finally:
        con.close()


def get_db() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency: yield an open connection, close it on exit."""
    con = get_connection()
    try:
        yield con
    finally:
        con.close()


@contextlib.contextmanager
def transaction(con: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Context manager: BEGIN IMMEDIATE, COMMIT on exit, ROLLBACK on any exception."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
