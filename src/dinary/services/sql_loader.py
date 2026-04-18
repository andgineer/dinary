"""SQL file loader and typed row mapper for DuckDB queries.

Loads .sql files from the dinary.sql package and maps DuckDB tuple results
to typed dataclass instances using con.description column names.
"""

import dataclasses
from importlib import resources
from typing import TypeVar

import duckdb

_cache: dict[str, str] = {}

T = TypeVar("T")


def load_sql(name: str) -> str:
    """Load a .sql file from the dinary.sql package, cached after first read."""
    if name not in _cache:
        text = resources.files("dinary.sql").joinpath(name).read_text(encoding="utf-8")
        _cache[name] = text.strip()
    return _cache[name]


def _validate_columns(cls: type[T], columns: list[str]) -> None:
    """Assert that SQL columns match dataclass fields exactly. Called once per query."""
    fields = {f.name for f in dataclasses.fields(cls)}  # pyrefly: ignore[bad-argument-type]
    if set(columns) != fields:
        missing = fields - set(columns)
        extra = set(columns) - fields
        raise RuntimeError(
            f"SQL/dataclass mismatch for {cls.__name__}: missing={missing}, extra={extra}",
        )


def _columns(con: duckdb.DuckDBPyConnection) -> list[str]:
    return [desc[0] for desc in con.description]


def fetchone_as(
    cls: type[T],
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list | None = None,
) -> T | None:
    """Execute SQL, map the first result row to a dataclass instance."""
    row = con.execute(sql, params or []).fetchone()
    columns = _columns(con)
    _validate_columns(cls, columns)
    if row is None:
        return None
    return cls(**dict(zip(columns, row, strict=False)))


def fetchall_as(
    cls: type[T],
    con: duckdb.DuckDBPyConnection,
    sql: str,
    params: list | None = None,
) -> list[T]:
    """Execute SQL, map all result rows to dataclass instances."""
    rows = con.execute(sql, params or []).fetchall()
    columns = _columns(con)
    _validate_columns(cls, columns)
    if not rows:
        return []
    return [cls(**dict(zip(columns, r, strict=False))) for r in rows]
