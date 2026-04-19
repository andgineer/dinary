"""DuckDB repository: config.duckdb (catalog) and budget_YYYY.duckdb (ledger).

Process-wide single engine model
---------------------------------
DuckDB enforces a per-process invariant that any database file may only
be opened by ONE engine in that process — opening it a second time (even
via `ATTACH` from a different `duckdb.connect()` instance) raises

    BinderException: Unique file handle conflict: Cannot attach
    "config" - the database file ... is already attached by database
    "config"

We hit this in production right after the 3D rollout: the async
sheet-sync worker holds a `budget_YYYY` connection (which previously
ATTACHed config READ_ONLY inside its own engine) for the duration of a
gspread roundtrip. While that worker was parked on the network, every
`POST /api/expenses` whose `convert_to_eur` step needed a config RW
connection turned into a 500.

The fix: keep ONE DuckDB engine for the whole process. `config.duckdb`
and every needed `budget_YYYY.duckdb` are ATTACHed onto that engine
exactly once, with the same lifetime as the engine. `get_*_connection`
returns a fresh `cursor()` of the engine, with `USE config` or
`USE budget_<year>` set so existing SQL — which assumes the relevant
DB is the "current" one — keeps working. Cursors share the engine but
have independent transaction state, so `BEGIN/COMMIT/ROLLBACK` semantics
in `insert_expense`, `claim_sync_job`, `reserve_expense_id_year`, etc.
are unchanged.

Migration coordination
----------------------
Yoyo runs migrations through its own `duckdb.connect(path)` call —
which would itself trigger the BinderException if the singleton already
has the file attached. `init_config_db` / `init_budget_db` therefore
DETACH the file from the singleton (if attached) before invoking yoyo,
and the next `get_*_connection` call lazily re-ATTACHes. The same
contract applies to destructive `unlink()` paths: `release_config_attach`
and `release_budget_attach` are the public hooks that
`seed_config.rebuild_config_from_sheets` and `import_sheet.import_year`
call before deleting a DB file out from under us.
"""

import dataclasses
import logging
import threading
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import duckdb

from dinary.services import db_migrations
from dinary.services.sql_loader import fetchall_as, fetchone_as, load_sql

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")

CONFIG_DB = DATA_DIR / "config.duckdb"

# ---------------------------------------------------------------------------
# Process-wide DuckDB engine (see module docstring)
# ---------------------------------------------------------------------------

_engine_lock = threading.Lock()


class _EngineState:
    """Mutable container for the singleton-engine bookkeeping.

    A class instance lets us mutate state inside helper functions
    without `global` declarations at every call site (pylint PLW0603
    flags the `global` keyword for good reason — it's the most common
    source of "why is this changing under me?" bugs). The module-level
    `_engine_state` instance below is the single source of truth.
    """

    __slots__ = ("attached_budget_years", "config_attached", "engine")

    def __init__(self) -> None:
        self.engine: duckdb.DuckDBPyConnection | None = None
        self.config_attached: bool = False
        self.attached_budget_years: set[int] = set()


_engine_state = _EngineState()


def _ensure_engine() -> duckdb.DuckDBPyConnection:
    """Lazily create the process-wide `:memory:` engine. Caller holds `_engine_lock`."""
    if _engine_state.engine is None:
        # The primary DB is `:memory:` so there is no on-disk file to
        # collide with anything else; config and budget DBs are
        # exclusively reached through ATTACH on this engine.
        _engine_state.engine = duckdb.connect(":memory:")
    return _engine_state.engine


def _ensure_config_attached_locked() -> duckdb.DuckDBPyConnection:
    engine = _ensure_engine()
    if not _engine_state.config_attached:
        # ATTACH does not accept the path as a bound parameter; SQL-escape
        # the single quote for paths that legally contain it (macOS user
        # dirs, pytest tmp paths under `O'Brien` etc.).
        config_path_sql = str(CONFIG_DB).replace("'", "''")
        engine.execute(f"ATTACH '{config_path_sql}' AS config (READ_WRITE)")
        _engine_state.config_attached = True
    return engine


def _ensure_budget_attached_locked(year: int) -> duckdb.DuckDBPyConnection:
    engine = _ensure_config_attached_locked()
    if year not in _engine_state.attached_budget_years:
        path_sql = str(budget_path(year)).replace("'", "''")
        engine.execute(f"ATTACH '{path_sql}' AS budget_{year} (READ_WRITE)")
        _engine_state.attached_budget_years.add(year)
    return engine


def release_config_attach() -> None:
    """DETACH config from the singleton engine (no-op if not attached).

    Public so destructive paths that `unlink()` `config.duckdb` (the
    `inv rebuild-catalog` flow inside `seed_config.rebuild_config_from_sheets`)
    can release the file handle BEFORE the unlink. The next
    `get_config_connection` call re-ATTACHes lazily against the new
    file. Without this, a still-attached DuckDB would either prevent
    the unlink (Windows) or leave a dangling file handle pointing at
    the deleted inode (POSIX), and the next ATTACH would refuse with
    BinderException.
    """
    with _engine_lock:
        if _engine_state.engine is None or not _engine_state.config_attached:
            return
        # If a budget DB is still attached, it holds an internal
        # reference to config (DuckDB won't let us DETACH config while
        # there are queries pending). Detach those first to avoid the
        # `Cannot detach database "config" because it is referenced`
        # error class. The next get_budget_connection re-ATTACHes them.
        for year in list(_engine_state.attached_budget_years):
            try:
                _engine_state.engine.execute(f"DETACH budget_{year}")
            except duckdb.Error:
                logger.exception(
                    "Failed to DETACH budget_%d before releasing config",
                    year,
                )
            _engine_state.attached_budget_years.discard(year)
        try:
            _engine_state.engine.execute("DETACH config")
        except duckdb.Error:
            logger.exception("Failed to DETACH config in release_config_attach")
        _engine_state.config_attached = False


def release_budget_attach(year: int) -> None:
    """DETACH a budget_YYYY DB from the singleton engine (no-op if not attached).

    Public for the same reason as `release_config_attach`: the
    `import_sheet.import_year` bootstrap path `unlink()`s the budget DB
    file before re-creating it under the (possibly new) schema, and the
    file must be released from the engine first. Symmetric to the
    config helper; safe to call when nothing is attached.
    """
    with _engine_lock:
        if _engine_state.engine is None or year not in _engine_state.attached_budget_years:
            return
        try:
            _engine_state.engine.execute(f"DETACH budget_{year}")
        except duckdb.Error:
            logger.exception("Failed to DETACH budget_%d", year)
        _engine_state.attached_budget_years.discard(year)


# Default time after which an in_progress sheet_sync_jobs claim is considered
# stale and may be reclaimed by a new worker. Workers should pass an explicit
# `stale_before` (now - timeout); this constant is the canonical timeout used
# by callers that don't override it.
DEFAULT_CLAIM_STALE_TIMEOUT = timedelta(minutes=5)


def best_effort_rollback(con: duckdb.DuckDBPyConnection, *, context: str) -> None:
    """Issue ROLLBACK without letting a failed rollback mask the original error.

    Use in `except Exception: best_effort_rollback(con, context=...); raise`
    blocks. If ROLLBACK itself raises (broken connection, txn already aborted
    by an earlier error inside the same statement, etc.) we log the
    secondary failure and swallow it so the caller's `raise` re-raises the
    *original* exception. The original cause is what an operator needs in
    the traceback; a "ROLLBACK failed because connection is closed"
    secondary error just buries the real bug.
    """
    try:
        con.execute("ROLLBACK")
    except Exception:
        logger.exception("Best-effort ROLLBACK failed (context: %s)", context)


# ---------------------------------------------------------------------------
# Row types for typed query results
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class MappingRow:
    id: int
    category_id: int
    event_id: int | None


@dataclasses.dataclass(slots=True)
class ExpenseRow:
    id: str
    datetime: datetime
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    category_id: int
    event_id: int | None
    comment: str | None
    sheet_category: str | None
    sheet_group: str | None


@dataclasses.dataclass(slots=True)
class ExistingExpenseRow:
    amount: Decimal
    amount_original: Decimal
    currency_original: str
    category_id: int
    event_id: int | None
    comment: str | None
    datetime: datetime
    sheet_category: str | None
    sheet_group: str | None


@dataclasses.dataclass(slots=True)
class ImportSourceRow:
    year: int
    spreadsheet_id: str
    worksheet_name: str
    layout_key: str
    notes: str | None
    income_worksheet_name: str = ""
    income_layout_key: str = ""


@dataclasses.dataclass(slots=True)
class IdNameRow:
    id: int
    name: str


@dataclasses.dataclass(slots=True)
class CategoryListRow:
    id: int
    name: str
    group_id: int
    group_name: str
    group_sort_order: int


@dataclasses.dataclass(slots=True)
class ForwardProjectionCandidateRow:
    id: int
    sheet_category: str
    sheet_group: str
    event_id: int | None
    tag_ids: list[int]


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def budget_path(year: int) -> Path:
    """Return the absolute path of `budget_YYYY.duckdb`.

    Public so callers like `import_sheet.import_year` can unlink the file
    before re-creating it under a new schema (yoyo otherwise treats the
    pre-existing migration row as already applied).
    """
    return DATA_DIR / f"budget_{year}.duckdb"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def init_config_db() -> None:
    """Create or migrate config.duckdb to the latest schema.

    Releases the singleton's ATTACH on config first: yoyo opens its own
    `duckdb.connect(CONFIG_DB)` to apply migrations, which would clash
    with the singleton's still-attached handle (the BinderException
    that motivated the singleton-engine refactor). The next
    `get_config_connection` re-ATTACHes lazily.
    """
    ensure_data_dir()
    release_config_attach()
    db_migrations.migrate_config_db(CONFIG_DB)


def init_budget_db(year: int) -> Path:
    """Create or migrate a yearly budget DB and return its path.

    Same migration-coordination contract as `init_config_db`: yoyo
    needs exclusive file access, so we release the singleton's ATTACH
    on this year first.
    """
    ensure_data_dir()
    path = budget_path(year)
    created = not path.exists()
    release_budget_attach(year)
    db_migrations.migrate_budget_db(path)
    if created:
        logger.info("Created %s", path)
    return path


def get_budget_connection(year: int) -> duckdb.DuckDBPyConnection:
    """Return a cursor of the singleton engine pointed at `budget_<year>`.

    The cursor has `USE budget_<year>` set so existing SQL — which
    treats `expenses` / `expense_tags` / `sheet_sync_jobs` as the
    "current" tables — keeps working. Cross-DB references to config
    use the `config.X` qualifier and resolve through the same engine,
    so no ATTACH inside the budget cursor is needed (and that's
    exactly the ATTACH that produced the BinderException before this
    refactor).

    The caller is responsible for closing the returned cursor;
    `cursor.close()` only closes the cursor, leaving the underlying
    engine and its ATTACHes intact for subsequent callers.
    """
    init_budget_db(year)
    with _engine_lock:
        engine = _ensure_budget_attached_locked(year)
    cur = engine.cursor()
    cur.execute(f"USE budget_{year}")
    return cur


def get_config_connection(read_only: bool = True) -> duckdb.DuckDBPyConnection:  # noqa: ARG001 — kept for API compatibility
    """Return a cursor of the singleton engine pointed at `config`.

    The `read_only` parameter is accepted for backward compatibility
    with the pre-singleton API but is now informational: the
    singleton always attaches config READ_WRITE so a single engine
    can serve both API writes (rate cache via `convert_to_eur`,
    `expense_id_registry` mutation) and async-drain reads without
    DuckDB's "unique file handle" conflict. Callers that previously
    relied on DuckDB rejecting writes on a `read_only=True` connection
    will silently succeed; in practice no production caller does this
    — the read-only flag was a hint, not a contract.
    """
    with _engine_lock:
        engine = _ensure_config_attached_locked()
    cur = engine.cursor()
    cur.execute("USE config")
    return cur


def iter_budget_years() -> list[int]:
    """Return sorted list of years for which a budget_YYYY.duckdb file exists."""
    if not DATA_DIR.exists():
        return []
    years: list[int] = []
    for path in DATA_DIR.glob("budget_*.duckdb"):
        stem = path.stem.replace("budget_", "")
        try:
            years.append(int(stem))
        except ValueError:
            continue
    return sorted(years)


# ---------------------------------------------------------------------------
# Catalog (config) queries
# ---------------------------------------------------------------------------


def list_categories(con: duckdb.DuckDBPyConnection) -> list[CategoryListRow]:
    """Return all categories with embedded group info, ordered by group sort then name."""
    return fetchall_as(CategoryListRow, con, load_sql("list_categories.sql"))


def get_catalog_version(con: duckdb.DuckDBPyConnection) -> int:
    """Return the current catalog_version from app_metadata singleton."""
    row = con.execute("SELECT catalog_version FROM app_metadata WHERE id = 1").fetchone()
    if row is None:
        msg = "app_metadata singleton row is missing"
        raise RuntimeError(msg)
    return int(row[0])


def _set_catalog_version(con: duckdb.DuckDBPyConnection, value: int) -> None:
    """Module-internal: only `rebuild-catalog` flow should call this (via tasks.py)."""
    con.execute("UPDATE app_metadata SET catalog_version = ? WHERE id = 1", [value])


def get_import_source(year: int) -> ImportSourceRow | None:
    """Look up the import source metadata for a given year."""
    con = get_config_connection(read_only=True)
    try:
        return fetchone_as(
            ImportSourceRow,
            con,
            "SELECT year, spreadsheet_id, worksheet_name, layout_key, notes,"
            " income_worksheet_name, income_layout_key"
            " FROM sheet_import_sources WHERE year = ?",
            [year],
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Sheet mapping resolution (used by historical import only)
# ---------------------------------------------------------------------------


def resolve_mapping(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
) -> MappingRow | None:
    """Look up (sheet_category, sheet_group) in sheet_mapping via ATTACHed config (year=0)."""
    return fetchone_as(MappingRow, con, load_sql("resolve_mapping.sql"), [category, group])


def resolve_mapping_for_year(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
    year: int,
) -> MappingRow | None:
    """Look up (sheet_category, sheet_group) with year-scoped override support.

    Prefers an exact year match; falls back to year=0 (default).
    """
    return fetchone_as(
        MappingRow,
        con,
        load_sql("resolve_mapping_for_year.sql"),
        [category, group, year],
    )


def get_mapping_tag_ids(
    con: duckdb.DuckDBPyConnection,
    mapping_id: int,
) -> list[int]:
    """Return tag_ids attached to a given sheet_mapping row, ordered by tag_id."""
    rows = con.execute(
        "SELECT tag_id FROM config.sheet_mapping_tags WHERE mapping_id = ? ORDER BY tag_id",
        [mapping_id],
    ).fetchall()
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Forward projection: pick (sheet_category, sheet_group) for a new expense.
# Used by the async sheet-append worker. Always targets the latest configured
# sheet year, never `expense.datetime.year`.
# ---------------------------------------------------------------------------


def forward_projection(
    con: duckdb.DuckDBPyConnection,
    *,
    latest_sheet_year: int,
    category_id: int,
    event_id: int | None,
    tag_ids: list[int] | set[int] | tuple[int, ...],
) -> tuple[str, str] | None:
    """Resolve `(category_id, event_id, tag set)` to `(sheet_category, sheet_group)`.

    Lookup order against `sheet_mapping` + `sheet_mapping_tags` inside
    `latest_sheet_year`:
      1. exact match: same `category_id`, same `event_id` (NULL matches NULL),
         and same tag-id set as the expense;
      2. fallback: first `sheet_mapping` row with the same `category_id`,
         ignoring `event_id` and tags.
    Ties inside the same preference bucket are broken by `sheet_mapping.id ASC`.
    """
    expected_tags = sorted(int(t) for t in tag_ids)
    candidates = fetchall_as(
        ForwardProjectionCandidateRow,
        con,
        load_sql("forward_projection.sql"),
        [latest_sheet_year, category_id],
    )
    if not candidates:
        return None

    for cand in candidates:
        cand_tags = sorted(int(t) for t in cand.tag_ids)
        if cand.event_id == event_id and cand_tags == expected_tags:
            return (cand.sheet_category, cand.sheet_group)

    first = candidates[0]
    return (first.sheet_category, first.sheet_group)


# ---------------------------------------------------------------------------
# expense_id_registry helpers (cross-year ownership of expense_id, in config.duckdb)
# ---------------------------------------------------------------------------


def reserve_expense_id_year(expense_id: str, year: int) -> tuple[int, bool]:
    """Atomically claim or look up the year that owns `expense_id`.

    Returns `(stored_year, newly_inserted)`:
      * `stored_year` is the year currently associated with the id after the
        call (may differ from `year` if the row already existed — the API
        layer turns that into a 409 cross-year reuse error).
      * `newly_inserted` is True iff this call performed the INSERT. The
        caller MUST `release_expense_id_year(expense_id)` if a downstream
        step fails, so the registry doesn't end up holding a phantom row
        for an expense that never made it into the budget DB.

    Concurrency: DuckDB doesn't give us a true row-level "SELECT FOR UPDATE",
    so two concurrent callers can both fall through the `SELECT NULL` branch
    and try to INSERT. The PK constraint on `expense_id` makes the loser's
    INSERT fail with `duckdb.ConstraintException`; we catch that here and
    re-read so the loser sees the same `(stored_year, False)` result it
    would have gotten if it had simply run a fraction of a second later.
    Without this, the loser would surface a 5xx instead of a clean 409.
    """
    con = get_config_connection(read_only=False)
    try:
        # `txn_active` lets the outer except skip its own rollback when
        # the inner ConstraintException path has already issued one. The
        # alternative — a second `best_effort_rollback` against an
        # already-closed txn — produces a misleading "ROLLBACK failed"
        # log that masks the real RuntimeError("vanished row") path.
        txn_active = True
        con.execute("BEGIN")
        try:
            row = con.execute(
                "SELECT year FROM expense_id_registry WHERE expense_id = ?",
                [expense_id],
            ).fetchone()
            if row is not None:
                con.execute("COMMIT")
                txn_active = False
                return int(row[0]), False
            try:
                con.execute(
                    "INSERT INTO expense_id_registry (expense_id, year) VALUES (?, ?)",
                    [expense_id, year],
                )
            except duckdb.ConstraintException:
                # Lost a race to a concurrent reserver; the row is now there.
                # Rollback the failed INSERT (ROLLBACK errors here would
                # mean the connection is unusable anyway, so we let them
                # propagate — unlike the outer except block, this path
                # MUST roll back to release the txn before the SELECT
                # below can succeed).
                con.execute("ROLLBACK")
                txn_active = False
                raced = con.execute(
                    "SELECT year FROM expense_id_registry WHERE expense_id = ?",
                    [expense_id],
                ).fetchone()
                if raced is None:
                    # Constraint fired but row vanished — should be impossible
                    # for a PK insert, but bubble up as-is rather than guess.
                    msg = (
                        f"expense_id_registry race for {expense_id!r}: PK "
                        "violation on INSERT but row not present on re-read"
                    )
                    raise RuntimeError(msg) from None
                return int(raced[0]), False
            con.execute("COMMIT")
            txn_active = False
            return year, True
        except Exception:
            if txn_active:
                best_effort_rollback(
                    con,
                    context=f"reserve_expense_id({expense_id!r})",
                )
            raise
    finally:
        con.close()


def get_registered_expense_year(expense_id: str) -> int | None:
    """Return the year currently registered for `expense_id`, or None if absent."""
    con = get_config_connection(read_only=True)
    try:
        row = con.execute(
            "SELECT year FROM expense_id_registry WHERE expense_id = ?",
            [expense_id],
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        con.close()


def release_expense_id_year(expense_id: str) -> None:
    """Remove an expense_id from the registry. Use only on rollback/error cleanup."""
    con = get_config_connection(read_only=False)
    try:
        con.execute("DELETE FROM expense_id_registry WHERE expense_id = ?", [expense_id])
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Expense insert (3D)
# ---------------------------------------------------------------------------


InsertExpenseResult = Literal["created", "duplicate", "conflict"]


def insert_expense(  # noqa: PLR0913, C901
    con: duckdb.DuckDBPyConnection,
    *,
    expense_id: str,
    expense_datetime: datetime,
    amount: float,
    amount_original: float,
    currency_original: str,
    category_id: int,
    event_id: int | None = None,
    comment: str = "",
    sheet_category: str | None = None,
    sheet_group: str | None = None,
    tag_ids: list[int] | None = None,
    enqueue_sync: bool = True,
) -> InsertExpenseResult:
    """Insert an expense + tags + queue row in one transaction.

    Returns 'created', 'duplicate', or 'conflict'.
    Validates `category_id` against ATTACHed config.categories and `event_id`
    against ATTACHed config.events. Tag IDs are validated against
    config.tags. Provenance pair `(sheet_category, sheet_group)` must be
    NULL/NULL or both non-NULL — this is a runtime invariant enforced here
    instead of a CHECK so the migration stays simple.
    """
    if (sheet_category is None) != (sheet_group is None):
        msg = (
            "sheet_category and sheet_group must be both NULL (runtime row) "
            "or both non-NULL (bootstrap-imported provenance row)"
        )
        raise ValueError(msg)

    tag_ids = tag_ids or []

    con.execute("BEGIN")
    try:
        if not con.execute(
            "SELECT 1 FROM config.categories WHERE id = ?",
            [category_id],
        ).fetchone():
            raise ValueError(f"category_id {category_id} not found in config.categories")
        if (
            event_id is not None
            and not con.execute(
                "SELECT 1 FROM config.events WHERE id = ?",
                [event_id],
            ).fetchone()
        ):
            raise ValueError(f"event_id {event_id} not found in config.events")
        for tid in tag_ids:
            if not con.execute(
                "SELECT 1 FROM config.tags WHERE id = ?",
                [tid],
            ).fetchone():
                raise ValueError(f"tag_id {tid} not found in config.tags")

        inserted = con.execute(
            load_sql("insert_expense.sql"),
            [
                expense_id,
                expense_datetime,
                amount,
                amount_original,
                currency_original,
                category_id,
                event_id,
                comment,
                sheet_category,
                sheet_group,
            ],
        ).fetchone()

        if inserted is not None:
            for tid in tag_ids:
                con.execute(
                    "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)"
                    " ON CONFLICT DO NOTHING",
                    [expense_id, tid],
                )
            if enqueue_sync:
                con.execute(
                    "INSERT INTO sheet_sync_jobs (expense_id, status) VALUES (?, 'pending')"
                    " ON CONFLICT DO NOTHING",
                    [expense_id],
                )
            con.execute("COMMIT")
            return "created"

        existing = fetchone_as(
            ExistingExpenseRow,
            con,
            load_sql("get_existing_expense.sql"),
            [expense_id],
        )
        assert existing is not None, f"expense {expense_id} vanished after ON CONFLICT"

        existing_tag_ids = sorted(
            int(r[0])
            for r in con.execute(
                "SELECT tag_id FROM expense_tags WHERE expense_id = ?",
                [expense_id],
            ).fetchall()
        )
        incoming_tag_ids = sorted(int(t) for t in tag_ids)

        stored = (
            existing.amount,
            existing.amount_original,
            existing.currency_original,
            existing.category_id,
            existing.event_id,
            existing.comment,
            existing.datetime,
            existing.sheet_category,
            existing.sheet_group,
            existing_tag_ids,
        )
        incoming = (
            Decimal(str(amount)),
            Decimal(str(amount_original)),
            currency_original,
            category_id,
            event_id,
            comment,
            expense_datetime,
            sheet_category,
            sheet_group,
            incoming_tag_ids,
        )

        # NOT best-effort: this is the SUCCESS path (no INSERT
        # happened, "duplicate"/"conflict" doesn't write). A failing
        # ROLLBACK here is the primary error — bubbling it out is
        # correct, masking it would leave the txn open.
        con.execute("ROLLBACK")

        if stored == incoming:
            return "duplicate"
        return "conflict"

    except Exception:
        # The outer except catches genuine failures (the SQL above
        # raised). Use best-effort rollback so a secondary ROLLBACK
        # failure doesn't replace the original error in the traceback.
        best_effort_rollback(con, context=f"insert_expense({expense_id!r})")
        raise


def get_expense_tags(con: duckdb.DuckDBPyConnection, expense_id: str) -> list[int]:
    """Return the tag_ids attached to an expense, sorted ascending."""
    rows = con.execute(
        "SELECT tag_id FROM expense_tags WHERE expense_id = ? ORDER BY tag_id",
        [expense_id],
    ).fetchall()
    return [int(r[0]) for r in rows]


def get_expense_by_id(con: duckdb.DuckDBPyConnection, expense_id: str) -> ExistingExpenseRow | None:
    """Read a stored expense row by id."""
    return fetchone_as(
        ExistingExpenseRow,
        con,
        load_sql("get_existing_expense.sql"),
        [expense_id],
    )


# ---------------------------------------------------------------------------
# sheet_sync_jobs queue helpers (keyed by expense_id, with atomic claim/release)
# ---------------------------------------------------------------------------


def enqueue_sync_job(con: duckdb.DuckDBPyConnection, expense_id: str) -> None:
    """Insert a `pending` row for `expense_id` (no-op if it already exists)."""
    con.execute(
        "INSERT INTO sheet_sync_jobs (expense_id, status) VALUES (?, 'pending')"
        " ON CONFLICT DO NOTHING",
        [expense_id],
    )


def list_sync_jobs(con: duckdb.DuckDBPyConnection) -> list[str]:
    """Return all expense_ids currently waiting in sheet_sync_jobs (any status)."""
    rows = con.execute("SELECT expense_id FROM sheet_sync_jobs ORDER BY expense_id").fetchall()
    return [str(r[0]) for r in rows]


def claim_sync_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    *,
    claim_token: str | None = None,
    now: datetime | None = None,
    stale_before: datetime | None = None,
) -> str | None:
    """Atomically claim `expense_id`. Returns the claim_token on success, None otherwise.

    A row is claimable if it is `pending`, OR if it is `in_progress` with
    `claimed_at < stale_before` (lease-style stale-claim recovery).
    """
    if claim_token is None:
        claim_token = uuid.uuid4().hex
    if now is None:
        now = datetime.now()
    if stale_before is None:
        stale_before = now - DEFAULT_CLAIM_STALE_TIMEOUT

    con.execute("BEGIN")
    try:
        row = con.execute(
            "SELECT status, claim_token, claimed_at FROM sheet_sync_jobs WHERE expense_id = ?",
            [expense_id],
        ).fetchone()
        if row is None:
            con.execute("COMMIT")
            return None
        status, _existing_token, claimed_at = row

        is_pending = status == "pending"
        is_stale = status == "in_progress" and claimed_at is not None and claimed_at < stale_before
        if not (is_pending or is_stale):
            con.execute("COMMIT")
            return None

        con.execute(
            "UPDATE sheet_sync_jobs SET status = 'in_progress',"
            " claim_token = ?, claimed_at = ? WHERE expense_id = ?",
            [claim_token, now, expense_id],
        )
        con.execute("COMMIT")
        return claim_token
    except duckdb.TransactionException:
        # DuckDB uses optimistic concurrency control; under concurrent
        # claim attempts on the same row the loser gets a transaction
        # conflict here. Treat that as "not claimable right now" — the
        # winner already moved the row to `in_progress`, so the next
        # sweep (or the in-flight worker) will own it. Returning None
        # avoids blowing up the sweep with a noisy exception that the
        # caller would have to translate back to "skip this row" anyway.
        best_effort_rollback(con, context=f"claim_sync_job({expense_id}) txn conflict")
        logger.debug("Transaction conflict claiming %s; another worker won", expense_id)
        return None
    except Exception:
        # Best-effort rollback: if ROLLBACK itself raises (broken
        # connection, constraint surfacing only at boundary, etc.) we
        # log and re-raise the *original* exception, not the secondary
        # one, so the caller's traceback points at the real cause
        # instead of a confusing rollback failure that masks it.
        best_effort_rollback(con, context=f"claim_sync_job({expense_id}) generic error")
        raise


def release_sync_claim(
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    claim_token: str,
) -> bool:
    """Release a held claim back to `pending` (only if `claim_token` still matches).

    Returns True if the row was released, False if not (different claimant
    has taken over, or the row no longer exists).
    """
    rows = con.execute(
        "UPDATE sheet_sync_jobs SET status = 'pending', claim_token = NULL, claimed_at = NULL"
        " WHERE expense_id = ? AND claim_token = ? RETURNING expense_id",
        [expense_id, claim_token],
    ).fetchall()
    return len(rows) > 0


def _delete_sync_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    *,
    claim_token: str | None,
) -> bool:
    """Single source of truth for deleting a `sheet_sync_jobs` row.

    `claim_token=None` skips the token check (force-delete by expense_id
    only). Factored out so a future column change to `sheet_sync_jobs`
    only updates one DELETE statement instead of two divergent ones; the
    public `clear_sync_job` and `force_clear_sync_job` are thin wrappers
    that document the two distinct legitimate use cases.
    """
    if claim_token is None:
        rows = con.execute(
            "DELETE FROM sheet_sync_jobs WHERE expense_id = ? RETURNING expense_id",
            [expense_id],
        ).fetchall()
    else:
        rows = con.execute(
            "DELETE FROM sheet_sync_jobs WHERE expense_id = ? AND claim_token = ?"
            " RETURNING expense_id",
            [expense_id, claim_token],
        ).fetchall()
    return len(rows) > 0


def clear_sync_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: str,
    claim_token: str,
) -> bool:
    """Delete the queue row after a successful append. Requires matching claim_token."""
    return _delete_sync_job(con, expense_id, claim_token=claim_token)


def force_clear_sync_job(con: duckdb.DuckDBPyConnection, expense_id: str) -> bool:
    """Delete the queue row by expense_id, ignoring claim_token.

    Use ONLY when the caller has definitively succeeded at the side-effect
    that the queue row was tracking (e.g. the Sheets append already
    happened) and a normal claim-token-protected `clear_sync_job` failed
    because the claim was stolen by a stale-claim sweep. Deleting
    unconditionally here prevents a *third* sheet append: the thief who
    stole the claim will (or already did) cause the second append, and
    leaving the row in place would cause the next sweep to claim and
    append a third copy.

    Returns True if a row was deleted.
    """
    return _delete_sync_job(con, expense_id, claim_token=None)


def get_month_expenses(
    con: duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    """Read all expenses for a given month."""
    return fetchall_as(ExpenseRow, con, load_sql("get_month_expenses.sql"), [year, month])
