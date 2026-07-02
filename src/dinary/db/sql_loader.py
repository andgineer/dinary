"""SQL file loader + typed row mapper. Every ``SELECT`` column consumed via
``fetchone_as``/``fetchall_as`` must carry an explicit ``AS <name>`` alias matching
the dataclass field — a computed expression (``COALESCE``, ``CASE``, ``strftime``...)
otherwise surfaces as raw expression source in ``cursor.description``, producing a
confusing SQL/dataclass mismatch error."""

import dataclasses
import sqlite3
from importlib import resources

_cache: dict[str, str] = {}


def load_sql(name: str) -> str:
    """Load a .sql file from the dinary.db.sql package, cached after first read."""
    if name not in _cache:
        text = resources.files("dinary.db.sql").joinpath(name).read_text(encoding="utf-8")
        _cache[name] = text.strip()
    return _cache[name]


def _validate_columns[T](cls: type[T], columns: list[str]) -> None:
    """Assert that SQL columns match dataclass fields exactly. Called once per query."""
    fields = {f.name for f in dataclasses.fields(cls)}  # pyrefly: ignore[bad-argument-type]
    if set(columns) != fields:
        missing = fields - set(columns)
        extra = set(columns) - fields
        raise RuntimeError(
            f"SQL/dataclass mismatch for {cls.__name__}: missing={missing}, extra={extra}",
        )


def fetchone_as[T](
    cls: type[T],
    con: sqlite3.Connection,
    sql: str,
    params: list | None = None,
) -> T | None:
    """Execute SQL, map the first result row to a dataclass instance."""
    cursor = con.execute(sql, params or [])
    columns = [desc[0] for desc in cursor.description]
    _validate_columns(cls, columns)
    row = cursor.fetchone()
    if row is None:
        return None
    return cls(**dict(zip(columns, row, strict=False)))


def fetchall_as[T](
    cls: type[T],
    con: sqlite3.Connection,
    sql: str,
    params: list | None = None,
) -> list[T]:
    """Execute SQL, map all result rows to dataclass instances."""
    cursor = con.execute(sql, params or [])
    columns = [desc[0] for desc in cursor.description]
    _validate_columns(cls, columns)
    rows = cursor.fetchall()
    if not rows:
        return []
    return [cls(**dict(zip(columns, r, strict=False))) for r in rows]
