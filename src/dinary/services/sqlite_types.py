"""SQLite adapter/converter registration for Decimal/date/datetime. Import for side effects.

PARSE_DECLTYPES caveat: converters only fire for bare column references, not
aggregates. ``SUM(amount)`` returns ``str`` or ``float``, not ``Decimal``.
Aggregate call sites must coerce explicitly: ``Decimal(str(row[i]))``.
"""

import sqlite3
from datetime import date, datetime
from decimal import Decimal


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

    * ``isolation_level=None`` puts the driver in autocommit mode so
      code that issues explicit ``BEGIN`` / ``COMMIT`` / ``ROLLBACK``
      statements behaves predictably instead of racing with the
      driver's implicit transaction management.
    * ``detect_types`` enables the adapter/converter machinery above.
    * ``check_same_thread=False`` allows the FastAPI event loop's
      thread-pool workers to share a connection pool across threads
      (each ``get_connection`` call opens a fresh file handle; the
      flag is defensive against accidental reuse across threads).
    * ``PRAGMA foreign_keys=ON`` is per-connection in SQLite and is
      applied here so referential integrity matches the DDL.
    * ``PRAGMA journal_mode=WAL`` is persistent on the file but we
      assert it on every connect so a misconfigured rollback from
      Litestream can't silently regress us to the default journal.
    * ``PRAGMA synchronous=NORMAL`` balances durability and throughput
      under WAL; Litestream replicates at checkpoint boundaries, so
      FULL is overkill here.
    * ``PRAGMA busy_timeout`` is **redundant** with the ``timeout=``
      argument we pass to ``sqlite3.connect`` (which already calls
      ``sqlite3_busy_timeout`` under the hood), but we emit it
      explicitly on every non-RO handle for two reasons: (1) it
      survives a mid-process call to ``PRAGMA busy_timeout=0`` that a
      future migration or diagnostic might issue, and (2) ``grep
      busy_timeout`` surfaces the applied value when an operator is
      auditing why a writer appeared to block. RO handles skip the
      PRAGMA because there are no other writers to contend with
      (only one writer can hold the WAL lock; readers never block
      on each other).

    ``read_only=True`` opens the DB via the ``file:...?mode=ro`` URI,
    which requires the DB file to already exist on disk. SQLite does
    not auto-create a file in read-only mode — callers that want
    "create if missing" should call with ``read_only=False`` first
    (typically via ``init_db()``) so the schema is materialized, and
    only then open read-only connections on top of it. This is by
    design: an operator opening ``inv sql`` on a fresh checkout must
    see an explicit "unable to open database file" error rather than
    silently querying an empty in-memory DB.
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
    # Belt-and-braces with sqlite3.connect(timeout=...); see the
    # PRAGMA-busy-timeout note in the docstring above.
    con.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
    return con
