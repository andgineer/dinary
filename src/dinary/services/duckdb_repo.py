"""DuckDB repository: config.duckdb (reference data) and budget_YYYY.duckdb (transactions).

Uses ATTACH for cross-DB referential integrity validation.
Read queries use Ibis expressions; write paths use raw DuckDB SQL.
"""

import dataclasses
import logging
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import ibis

from dinary.services import db_migrations
from dinary.services.ibis_helpers import fetchall_as, fetchone_as

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")

CONFIG_DB = DATA_DIR / "config.duckdb"

SYNTHETIC_EVENT_PREFIX = "отпуск-"
TRAVEL_GROUP = "путешествия"


# ---------------------------------------------------------------------------
# Row types for typed query results
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class MappingRow:
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    store_id: int | None
    tag_ids: list[int]

    def __post_init__(self) -> None:
        self.tag_ids = sorted(self.tag_ids) if self.tag_ids else []


@dataclasses.dataclass(slots=True)
class ExpenseRow:
    id: str
    datetime: datetime
    amount: Decimal
    currency: str
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    store_id: int | None
    comment: str | None
    tag_ids: list[int]

    def __post_init__(self) -> None:
        self.tag_ids = self.tag_ids if self.tag_ids else []


@dataclasses.dataclass(slots=True)
class SheetCategoryRow:
    sheet_category: str
    sheet_group: str


@dataclasses.dataclass(slots=True)
class ReverseMappingRow:
    sheet_category: str
    sheet_group: str
    tag_ids: list[int] | None


@dataclasses.dataclass(slots=True)
class EventIdRow:
    event_id: int


@dataclasses.dataclass(slots=True)
class ExistingExpenseRow:
    amount: Decimal
    currency: str
    category_id: int
    beneficiary_id: int | None
    event_id: int | None
    store_id: int | None
    comment: str | None
    datetime: datetime


@dataclasses.dataclass(slots=True)
class IdNameRow:
    id: int
    name: str


@dataclasses.dataclass(slots=True)
class CategoryRefRow:
    id: int
    name: str
    group_id: int


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def _budget_path(year: int) -> Path:
    return DATA_DIR / f"budget_{year}.duckdb"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_config_db() -> None:
    """Create or migrate config.duckdb to the latest schema."""
    ensure_data_dir()
    db_migrations.migrate_config_db(CONFIG_DB)


def init_budget_db(year: int) -> Path:
    """Create or migrate a yearly budget DB and return its path."""
    ensure_data_dir()
    path = _budget_path(year)
    created = not path.exists()
    db_migrations.migrate_budget_db(path)
    if created:
        logger.info("Created %s", path)
    return path


def get_budget_connection(year: int) -> duckdb.DuckDBPyConnection:
    """Open a connection to budget_YYYY.duckdb, creating it if needed.

    The caller is responsible for closing the connection.
    The connection has config.duckdb ATTACHed as 'config' (READ_ONLY).
    """
    path = init_budget_db(year)
    con = duckdb.connect(str(path))
    try:
        con.execute(
            f"ATTACH '{CONFIG_DB}' AS config (READ_ONLY)"
        )
    except Exception:
        con.close()
        raise
    return con


def get_config_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open a connection to config.duckdb."""
    return duckdb.connect(str(CONFIG_DB), read_only=read_only)


def close_connection(con: duckdb.DuckDBPyConnection) -> None:
    """Close a DuckDB connection and evict its cached Ibis backend."""
    _ibis_cache.pop(id(con), None)
    con.close()


_ibis_cache: dict[int, object] = {}


def _ibis_backend(con: duckdb.DuckDBPyConnection):
    """Wrap an existing DuckDB connection as an Ibis backend, preserving ATTACH state.

    Caches per connection id so repeated calls within the same function
    don't re-create the wrapper.
    """
    key = id(con)
    backend = _ibis_cache.get(key)
    if backend is not None:
        return backend
    backend = ibis.duckdb.from_connection(con)
    _ibis_cache[key] = backend
    return backend


# ---------------------------------------------------------------------------
# Ibis read queries
# ---------------------------------------------------------------------------


def resolve_mapping(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
) -> MappingRow | None:
    """Look up (category, group) in sheet_category_mapping via ATTACHed config.

    Returns MappingRow or None if not found.
    """
    ib = _ibis_backend(con)
    t = ib.table("sheet_category_mapping", database="config")
    expr = (
        t.filter((t.sheet_category == category) & (t.sheet_group == group))
        .select("category_id", "beneficiary_id", "event_id", "store_id", "tag_ids")
    )
    return fetchone_as(MappingRow, expr)


def resolve_travel_event(expense_date: date) -> int:
    """Find or create a synthetic travel event for the expense's year.

    Looks for an event named 'отпуск-YYYY' whose date range contains
    the expense date. Auto-creates one if missing.

    Operates directly on config.duckdb (not ATTACHed) to avoid handle conflicts.
    """
    year = expense_date.year
    event_name = f"{SYNTHETIC_EVENT_PREFIX}{year}"

    config_con = get_config_connection(read_only=False)
    try:
        ib = _ibis_backend(config_con)
        events = ib.table("events")
        expr = (
            events
            .filter(
                (events.name == event_name)
                & (events.date_from <= expense_date)
                & (events.date_to >= expense_date)
            )
            .select(events.id.name("event_id"))
        )
        row = fetchone_as(EventIdRow, expr)
        if row:
            return row.event_id

        max_id = events.id.max().execute()
        new_id = (max_id or 0) + 1
        ib.insert("events", [{
            "id": new_id,
            "name": event_name,
            "date_from": date(year, 1, 1),
            "date_to": date(year, 12, 31),
        }])
        logger.info("Auto-created synthetic travel event: %s (id=%d)", event_name, new_id)
        return new_id
    finally:
        close_connection(config_con)


def _validate_dimension(ib, table_name: str, dim_id: int, label: str) -> None:
    """Raise ValueError if dim_id doesn't exist in config.<table_name>."""
    t = ib.table(table_name, database="config")
    if t.filter(t.id == dim_id).count().execute() == 0:
        raise ValueError(f"{label} {dim_id} not found in config.{table_name}")


def insert_expense(
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    expense_datetime: datetime,
    amount: float,
    currency: str,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    store_id: int | None,
    tag_ids: list[int],
    comment: str,
) -> str:
    """Insert an expense, returning 'created', 'duplicate', or 'conflict'.

    Uses INSERT ... ON CONFLICT DO NOTHING RETURNING id for idempotent inserts.
    On PK conflict, compares stored values to detect true duplicates vs conflicts.
    Validates dimension IDs against ATTACHed config tables before inserting.
    """
    ib = _ibis_backend(con)

    con.execute("BEGIN")
    try:
        _validate_dimension(ib, "categories", category_id, "category_id")
        if beneficiary_id is not None:
            _validate_dimension(ib, "family_members", beneficiary_id, "beneficiary_id")
        if event_id is not None:
            _validate_dimension(ib, "events", event_id, "event_id")
        if store_id is not None:
            _validate_dimension(ib, "stores", store_id, "store_id")
        for tid in tag_ids:
            _validate_dimension(ib, "tags", tid, "tag_id")

        # ON CONFLICT DO NOTHING RETURNING — no Ibis equivalent
        inserted = con.execute(
            """
            INSERT INTO expenses (id, datetime, amount, currency, category_id,
                                  beneficiary_id, event_id, store_id, comment)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            RETURNING id
            """,
            [
                expense_id, expense_datetime, amount, currency,
                category_id, beneficiary_id, event_id, store_id, comment,
            ],
        ).fetchone()

        if inserted is not None:
            for tag_id in tag_ids:
                ib.insert("expense_tags", [{"expense_id": expense_id, "tag_id": tag_id}])
            year = expense_datetime.year
            month = expense_datetime.month
            # ON CONFLICT DO NOTHING — no Ibis equivalent
            con.execute(
                "INSERT INTO sheet_sync_jobs (year, month) VALUES (?, ?) ON CONFLICT DO NOTHING",
                [year, month],
            )
            con.execute("COMMIT")
            return "created"

        expenses_t = ib.table("expenses")
        existing = fetchone_as(
            ExistingExpenseRow,
            expenses_t
            .filter(expenses_t.id == expense_id)
            .select(
                "amount", "currency", "category_id", "beneficiary_id",
                "event_id", "store_id", "comment", "datetime",
            ),
        )

        et = ib.table("expense_tags")
        existing_tags_expr = (
            et.filter(et.expense_id == expense_id)
            .aggregate(tags=et.tag_id.collect())
        )
        tags_df = existing_tags_expr.execute()
        existing_tags = sorted(tags_df.iloc[0]["tags"]) if not tags_df.empty and tags_df.iloc[0]["tags"] is not None else []

        stored = (
            existing.amount, existing.currency, existing.category_id,
            existing.beneficiary_id, existing.event_id, existing.store_id,
            existing.comment, existing.datetime,
        )
        incoming = (
            Decimal(str(amount)), currency, category_id, beneficiary_id,
            event_id, store_id, comment, expense_datetime,
        )

        con.execute("ROLLBACK")

        if stored == incoming and existing_tags == sorted(tag_ids):
            return "duplicate"
        return "conflict"

    except Exception:
        con.execute("ROLLBACK")
        raise


def get_dirty_sync_jobs(con: duckdb.DuckDBPyConnection) -> list[tuple[int, int]]:
    """Return all (year, month) pairs pending sync."""
    ib = _ibis_backend(con)
    t = ib.table("sheet_sync_jobs")
    df = t.order_by(["year", "month"]).execute()
    return list(df.itertuples(index=False, name=None))


def clear_sync_job(con: duckdb.DuckDBPyConnection, year: int, month: int) -> None:
    """Remove a completed sync job. DELETE has no Ibis equivalent."""
    con.execute("DELETE FROM sheet_sync_jobs WHERE year = ? AND month = ?", [year, month])


def get_month_expenses(
    con: duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    """Read all expenses for a given month with their tags."""
    ib = _ibis_backend(con)
    expenses = ib.table("expenses")
    expense_tags = ib.table("expense_tags")

    tags_agg = (
        expense_tags
        .group_by("expense_id")
        .aggregate(tag_ids=expense_tags.tag_id.collect())
    )

    expr = (
        expenses
        .filter(
            (expenses.datetime.year() == year)
            & (expenses.datetime.month() == month)
        )
        .left_join(tags_agg, expenses.id == tags_agg.expense_id)
        .select(
            expenses.id,
            expenses.datetime,
            expenses.amount,
            expenses.currency,
            expenses.category_id,
            expenses.beneficiary_id,
            expenses.event_id,
            expenses.store_id,
            expenses.comment,
            ibis.coalesce(tags_agg.tag_ids, ibis.literal([], type="array<int64>")).name("tag_ids"),
        )
    )
    return fetchall_as(ExpenseRow, expr)


def list_sheet_categories(
    con: duckdb.DuckDBPyConnection,
) -> list[SheetCategoryRow]:
    """Return all (sheet_category, sheet_group) pairs ordered for display."""
    ib = _ibis_backend(con)
    t = ib.table("sheet_category_mapping")
    expr = t.select("sheet_category", "sheet_group").order_by(["sheet_group", "sheet_category"])
    return fetchall_as(SheetCategoryRow, expr)


def load_id_name_rows(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
) -> list[IdNameRow]:
    """Load (id, name) pairs from a config table."""
    ib = _ibis_backend(con)
    return fetchall_as(IdNameRow, ib.table(table_name).select("id", "name"))


def load_category_refs(
    con: duckdb.DuckDBPyConnection,
) -> list[CategoryRefRow]:
    """Load (id, name, group_id) from the categories table."""
    ib = _ibis_backend(con)
    return fetchall_as(CategoryRefRow, ib.table("categories").select("id", "name", "group_id"))


def insert_row(con: duckdb.DuckDBPyConnection, table_name: str, row: dict) -> None:
    """Insert a single row into a table via Ibis."""
    ib = _ibis_backend(con)
    ib.insert(table_name, [row])


def row_exists(con: duckdb.DuckDBPyConnection, table_name: str, **filters) -> bool:
    """Check if a row matching the given filters exists."""
    ib = _ibis_backend(con)
    t = ib.table(table_name)
    expr = t
    for col, val in filters.items():
        expr = expr.filter(getattr(t, col) == val)
    return expr.count().execute() > 0


def max_id(con: duckdb.DuckDBPyConnection, table_name: str) -> int:
    """Return the maximum id in a table, or 0 if empty."""
    ib = _ibis_backend(con)
    t = ib.table(table_name)
    result = t.id.max().execute()
    return result or 0


def reverse_lookup_mapping(
    con: duckdb.DuckDBPyConnection,
    category_id: int,
    beneficiary_id: int | None,
    event_id: int | None,
    store_id: int | None,
    tag_ids: list[int],
) -> tuple[str, str] | None:
    """Reverse-map a 5D expense back to (sheet_category, sheet_group).

    Two paths:
    1. Travel expenses (event_id references a synthetic event): match by
       category_id + TRAVEL_GROUP.
    2. All others: full 5D null-safe match.
    """
    ib = _ibis_backend(con)

    if event_id is not None:
        events = ib.table("events", database="config")
        is_travel = fetchone_as(
            EventIdRow,
            events
            .filter((events.id == event_id) & events.name.like(f"{SYNTHETIC_EVENT_PREFIX}%"))
            .select(events.id.name("event_id")),
        )
        if is_travel:
            mapping = ib.table("sheet_category_mapping", database="config")
            row = fetchone_as(
                SheetCategoryRow,
                mapping
                .filter((mapping.sheet_group == TRAVEL_GROUP) & (mapping.category_id == category_id))
                .select("sheet_category", "sheet_group"),
            )
            return (row.sheet_category, row.sheet_group) if row else None

    mapping = ib.table("sheet_category_mapping", database="config")

    ben_filter = (
        mapping.beneficiary_id == beneficiary_id
        if beneficiary_id is not None
        else mapping.beneficiary_id.isnull()
    )
    ev_filter = (
        mapping.event_id == event_id
        if event_id is not None
        else mapping.event_id.isnull()
    )
    st_filter = (
        mapping.store_id == store_id
        if store_id is not None
        else mapping.store_id.isnull()
    )

    expr = (
        mapping
        .filter(
            (mapping.category_id == category_id)
            & ben_filter
            & ev_filter
            & st_filter
        )
        .select("sheet_category", "sheet_group", "tag_ids")
    )
    rows = fetchall_as(ReverseMappingRow, expr)

    sorted_tags = sorted(tag_ids) if tag_ids else []
    for r in rows:
        mapping_tags = sorted(r.tag_ids) if r.tag_ids else []
        if mapping_tags == sorted_tags:
            return (r.sheet_category, r.sheet_group)

    return None
