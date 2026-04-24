"""SQL file loader and typed row mapper for the server's SQLite queries.

Loads .sql files from the dinary.sql package and maps stdlib sqlite3 tuple
results to typed dataclass instances using the cursor's ``description``
column names.

Contributor contract: every ``SELECT`` column consumed through
``fetchone_as`` / ``fetchall_as`` MUST carry an explicit ``AS <name>``
alias whose value matches the corresponding dataclass field. SQLite's
``cursor.description[i][0]`` returns whatever text appears in the
``SELECT`` list for the column — for a bare column reference
(``e.amount``) that is the column name, but for a computed expression
(``COALESCE(...)``, ``CASE ...``, ``json_group_array(...)``,
``strftime(...)``) it is the raw expression source. Without an alias
``_validate_columns`` would reject the query with a "SQL/dataclass
mismatch" error that points at a confusing expression string instead
of the field name the author meant. Aliases keep the SQL ↔ dataclass
pairing explicit and survive SQL-formatter round-trips unchanged.
"""

import dataclasses
import sqlite3
from importlib import resources

_cache: dict[str, str] = {}


def load_sql(name: str) -> str:
    """Load a .sql file from the dinary.sql package, cached after first read."""
    if name not in _cache:
        text = resources.files("dinary.sql").joinpath(name).read_text(encoding="utf-8")
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
