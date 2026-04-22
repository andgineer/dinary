"""DuckDB repository: single data/dinary.duckdb for catalog + ledger.

Process-wide single connection opened once on init, shared across all
callers via ``get_connection()``. No ATTACH, no per-year files.
"""

import dataclasses
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import duckdb

from dinary.config import settings
from dinary.services import db_migrations
from dinary.services.sql_loader import fetchall_as, fetchone_as, load_sql

logger = logging.getLogger(__name__)

# DB_PATH is derived from ``settings.data_path`` so operators can point
# the server (and ``inv`` tasks) at an alternate file via the
# ``DINARY_DATA_PATH`` environment variable (e.g. for smoke tests).
# DATA_DIR mirrors the parent directory so ``ensure_data_dir`` can
# create it. Tests that want a tmp DB monkeypatch both.
DB_PATH = Path(settings.data_path)
DATA_DIR = DB_PATH.parent

_CLAIM_STALE_FLOOR_SEC = 600.0


def default_claim_stale_timeout() -> timedelta:
    """Return the claim-stale cutoff used by the drain queue.

    Set to ``max(10 min, 2 × sheet_logging_drain_interval_sec)``. The
    2× multiplier keeps us safely above "one in-flight drain pass took
    slightly longer than one interval", which would otherwise let the
    next drain iteration steal a still-working claim, produce spurious
    ``RECOVERED_WITH_DUPLICATE`` outcomes, and fire the J-marker
    recovery path for no reason. The 10-minute floor handles the
    degenerate ``drain_interval ≤ 1s`` case (smoke tests).
    """
    return timedelta(
        seconds=max(
            _CLAIM_STALE_FLOOR_SEC,
            settings.sheet_logging_drain_interval_sec * 2,
        ),
    )


_conn: duckdb.DuckDBPyConnection | None = None


def best_effort_rollback(con: duckdb.DuckDBPyConnection, *, context: str) -> None:
    """Issue ROLLBACK without letting a failed rollback mask the original error."""
    try:
        con.execute("ROLLBACK")
    except Exception:
        logger.exception("Best-effort ROLLBACK failed (context: %s)", context)


def _is_unique_violation_of_client_expense_id(exc: BaseException) -> bool:
    """True iff ``exc`` is the UNIQUE race on ``expenses.client_expense_id``.

    Concurrent POSTs with the same UUID surface as a ``ConstraintException``
    *or* a ``TransactionException`` at either the INSERT statement
    (the winner's write is in-flight and their uncommitted snapshot
    holds the UNIQUE key) or at COMMIT (the winner committed between
    our INSERT and our COMMIT). DuckDB picks the class based on when
    in transaction lifecycle the conflict is detected; both carry the
    same duplicate-key diagnostic, and both mean "fall through to the
    compare path against the now-committed winner". Any other
    constraint/transaction error — FK violation on ``category_id``,
    disk I/O error, etc. — must propagate as-is.

    Implementation note. DuckDB's duplicate-key diagnostic doesn't
    include the column name (just the offending value), so we key off
    the "unique / primary-key / duplicate-key" phrasing. This is
    safe-but-permissive: within ``insert_expense`` the statement-level
    ``ON CONFLICT (client_expense_id) DO NOTHING`` target already
    scopes the only silently-absorbed conflict to that column; the
    auto-incremented ``expenses.id`` is never supplied by callers,
    and the sibling ``expense_tags`` / ``sheet_logging_jobs`` inserts
    are each scoped to their own PK (``ON CONFLICT (expense_id,
    tag_id)`` / ``ON CONFLICT (expense_id)`` respectively), so a
    future UNIQUE added to either of those tables would raise
    cleanly instead of being laundered through this classifier.

    The explicit ``"foreign"``-keyword exclusion is **defensive**, not
    currently load-bearing: as of the DuckDB version this refactor
    was written against, FK-violation messages look like
    ``"Constraint Error: Violates foreign key constraint because key
    ... does not exist in the referenced table"`` and carry none of
    our positive keywords. The carve-out is a cheap forward-compat
    hedge — if a future DuckDB release rewords FK diagnostics to
    mention "primary key" (the parent's column) or "unique
    constraint" (the referenced UNIQUE index), the guard prevents a
    true FK violation from being silently laundered into a
    duplicate/conflict response. Keep the guard in sync with the
    ``test_classifies_real_fk_violation_as_not_a_race`` test: if
    DuckDB ever emits an FK message without the word ``foreign``,
    that test fails loudly, and this classifier needs a new
    discriminator.
    """
    message = str(exc).lower()
    if "foreign" in message:
        return False
    return "duplicate key" in message or "unique constraint" in message or "primary key" in message


# Exception classes DuckDB may raise for the ``client_expense_id`` UNIQUE
# race, at either the INSERT statement or the COMMIT. Kept as a module
# constant so the ``insert_expense`` try/except blocks stay symmetric:
# historically only COMMIT needed both classes, but DuckDB is free to
# pick either at either point, and the cost of the extra class at
# INSERT is zero (``_is_unique_violation_of_client_expense_id`` is the
# real decision helper; the except clause just gates which errors it
# gets to inspect).
_DUCKDB_RACE_EXCS: tuple[type[duckdb.Error], ...] = (
    duckdb.ConstraintException,
    duckdb.TransactionException,
)


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class MappingRow:
    id: int
    category_id: int
    event_id: int | None


@dataclasses.dataclass(slots=True)
class ExpenseRow:
    id: int
    client_expense_id: str | None
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
    id: int
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
class LoggingProjectionCandidateRow:
    """A single ``sheet_mapping`` candidate row with its required tag set.

    ``category_id`` / ``event_id`` are ``None`` for wildcard rows (match
    any category / any event respectively). ``sheet_category`` /
    ``sheet_group`` carry either a literal target or the ``'*'``
    wildcard sentinel ("don't decide here"); the Python resolver in
    ``logging_projection`` picks the first non-wildcard value per
    column scanning rows in ``row_order`` ASC.
    """

    row_order: int
    category_id: int | None
    event_id: int | None
    sheet_category: str
    sheet_group: str
    tag_ids: list[int]


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_db_path() -> Path:
    """Return the effective DB path (allows test monkeypatching of DB_PATH)."""
    return DB_PATH


def init_db() -> None:
    """Create or migrate dinary.duckdb to the latest schema, then open the connection.

    Must be called once per process before ``get_connection()``. The FastAPI
    lifespan in ``dinary.main`` invokes it on startup; ``invoke`` tasks and
    tests do so explicitly (see ``tests/conftest.py::_reset_duckdb_connection``).
    """
    global _conn  # noqa: PLW0603
    ensure_data_dir()
    if _conn is not None:
        _conn.close()
        _conn = None
    db_migrations.migrate_db(_get_db_path())
    _conn = duckdb.connect(str(_get_db_path()))
    _reconcile_accounting_currency(_conn)


def _reconcile_accounting_currency(con: duckdb.DuckDBPyConnection) -> None:
    """Reconcile ``settings.accounting_currency`` with the DB anchor.

    Accounting-currency is a DB-wide invariant: every ``expenses.amount``
    and ``income.amount`` row on disk is denominated in it. Flipping the
    setting mid-life (e.g. accidentally writing
    ``DINARY_ACCOUNTING_CURRENCY=RSD`` into ``.deploy/.env``) would
    silently start persisting new rows in the new unit while existing
    rows stay in the old one, quietly corrupting every subsequent sum,
    report, and sheet-logging RSD derivation.

    Source-of-truth model:

    * ``DINARY_ACCOUNTING_CURRENCY`` (env var -> ``settings.accounting_currency``)
      is a **first-deploy-only** seed. It is consulted only to populate
      the anchor row on the very first ``init_db`` against an empty DB.
    * ``app_metadata.accounting_currency`` is the **runtime source of
      truth**. Once populated it is authoritative; subsequent boots read
      it back and broadcast the value to the rest of the codebase via
      the ``settings`` proxy. Operators can (and should) omit the env
      var on steady-state servers.

    Resolution matrix:

    * Row absent + env non-empty -> seed: INSERT uppercased env value.
    * Row absent + env empty -> ``RuntimeError`` (no seed source; the
      operator must tell us what currency the fresh ledger lives in).
    * Row present + env empty -> take DB value silently. The happy path
      on an operational server that has forgotten about the env var.
    * Row present + env matches (case-insensitive) -> no-op, but snap
      ``settings.accounting_currency`` to the uppercased canonical form.
    * Row present + env differs -> ``RuntimeError`` with both values
      named, actionable next steps. This is the typo-guard: we
      deliberately refuse to auto-correct either direction because a
      silent fix would mask the very foot-gun the anchor exists to
      defend against. Intentional migrations must convert every
      ``expenses`` / ``income`` row AND update the anchor row manually.

    Side effect: mutates ``settings.accounting_currency`` to the
    resolved uppercased value so existing call sites that read it
    (sheet_logging, reports, imports) automatically track the DB
    anchor without each needing a point-of-use DB lookup.
    """
    desired = settings.accounting_currency.strip().upper()

    row = con.execute(
        "SELECT value FROM app_metadata WHERE key = 'accounting_currency'",
    ).fetchone()

    if row is None:
        if not desired:
            msg = (
                "Fresh dinary.duckdb and settings.accounting_currency is empty; "
                "refusing to seed an unknown accounting currency. Set "
                "DINARY_ACCOUNTING_CURRENCY in .deploy/.env to a valid ISO-4217 "
                "code (e.g. EUR) for the first deploy; subsequent runs can omit "
                "it and the value will be read back from app_metadata."
            )
            raise RuntimeError(msg)
        con.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('accounting_currency', ?)",
            [desired],
        )
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


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a per-call cursor on the shared singleton connection.

    **Concurrency model.** DuckDB allows at most one writer per file
    across processes, so the server runs a single uvicorn worker. The
    cursor returned here shares the underlying engine (and its
    write-set) with the singleton connection, but carries its own
    transaction state — ``BEGIN``/``COMMIT`` on one cursor does not
    affect another. Multiple cursors can run simultaneously because
    ``POST /api/expenses`` offloads its blocking DB work via
    ``asyncio.to_thread``, so several thread-pool workers may each
    hold a cursor at once.

    **UNIQUE races are real and are recovered, not prevented.** Under
    real concurrent writers (e.g. two PWA clients — or one PWA + one
    retry — submitting the same ``client_expense_id`` through the
    event loop at the same time), ``ON CONFLICT DO NOTHING`` alone
    isn't enough: DuckDB's MVCC only absorbs conflicts with rows
    already committed at statement time, so racing cursors can both
    see "no row yet" and the loser later surfaces either as a
    ``ConstraintException`` at INSERT or a ``TransactionException``
    at COMMIT (DuckDB picks the class based on when it notices the
    conflict). ``insert_expense`` catches both classes, issues the
    explicit ``ROLLBACK`` DuckDB needs to clear the aborted cursor,
    and drops through to a compare-outside-tx path against the
    committed winner. Any new writer module that takes a cursor from
    here and uses ``ON CONFLICT`` on a user-supplied UNIQUE key must
    apply the same recovery pattern; relying on "single-worker server
    means no contention" is wrong because of ``asyncio.to_thread``.

    **Lifecycle.** ``get_connection()`` will lazily open the DB file
    without running migrations — callers that need a schema-guaranteed
    DB (e.g. ``dinary.main`` lifespan, ``invoke migrate``, tests) must
    call ``init_db()`` first. The returned cursor **must** be closed by
    the caller (``try ... finally: con.close()``).
    """
    global _conn  # noqa: PLW0603
    if _conn is None:
        _conn = duckdb.connect(str(_get_db_path()))
    return _conn.cursor()


def close_connection() -> None:
    """Close the singleton connection (for clean shutdown in tests)."""
    global _conn  # noqa: PLW0603
    if _conn is not None:
        _conn.close()
        _conn = None


# ---------------------------------------------------------------------------
# Catalog queries
# ---------------------------------------------------------------------------


def list_categories(con: duckdb.DuckDBPyConnection) -> list[CategoryListRow]:
    """Return active categories with active group info, ordered by group sort then name."""
    return fetchall_as(CategoryListRow, con, load_sql("list_categories.sql"))


def get_catalog_version(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute(
        "SELECT value FROM app_metadata WHERE key = 'catalog_version'",
    ).fetchone()
    if row is None:
        msg = "app_metadata 'catalog_version' key is missing"
        raise RuntimeError(msg)
    return int(row[0])


def set_catalog_version(con: duckdb.DuckDBPyConnection, value: int) -> None:
    """Public write for ``app_metadata.catalog_version``.

    Only two callers are expected: ``seed_config._bump_catalog_version``
    (the ``inv import-catalog`` path) and ``catalog_writer._commit_with_bump``
    (the admin-API path). Every other module is expected to go through
    one of those.
    """
    con.execute(
        "UPDATE app_metadata SET value = ? WHERE key = 'catalog_version'",
        [str(value)],
    )


# Backward-compatible alias (previous name was underscored-private).
_set_catalog_version = set_catalog_version


def get_category_name(con: duckdb.DuckDBPyConnection, category_id: int) -> str | None:
    row = con.execute(
        "SELECT name FROM categories WHERE id = ?",
        [category_id],
    ).fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# Sheet mapping resolution (import path)
# ---------------------------------------------------------------------------


def resolve_mapping(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
) -> MappingRow | None:
    return fetchone_as(MappingRow, con, load_sql("resolve_mapping.sql"), [category, group])


def resolve_mapping_for_year(
    con: duckdb.DuckDBPyConnection,
    category: str,
    group: str,
    year: int,
) -> MappingRow | None:
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
    rows = con.execute(
        "SELECT tag_id FROM import_mapping_tags WHERE mapping_id = ? ORDER BY tag_id",
        [mapping_id],
    ).fetchall()
    return [int(r[0]) for r in rows]


# ---------------------------------------------------------------------------
# Logging projection (3D -> 2D for sheet logging)
# ---------------------------------------------------------------------------


_PROJECTION_WILDCARD = "*"


def logging_projection(
    con: duckdb.DuckDBPyConnection,
    *,
    category_id: int,
    event_id: int | None,
    tag_ids: list[int] | set[int] | tuple[int, ...],
) -> tuple[str, str] | None:
    """Resolve ``(category_id, event_id, tag set)`` to ``(sheet_category, sheet_group)``.

    Sources from ``sheet_mapping`` (owned by the ``map`` worksheet tab
    and ``sheet_mapping.py``). Semantics: scan rows in ``row_order``
    ASC, keep only rows whose ``category_id`` / ``event_id`` / required
    tag set is compatible with the expense, and per output column pick
    the first non-``'*'`` value we see. ``NULL`` on ``category_id`` /
    ``event_id`` is a wildcard (matches anything including no event);
    ``'*'`` on ``sheet_category`` / ``sheet_group`` means "don't
    decide here".

    Fallbacks are applied per column independently: if the resolver
    did not pick a ``sheet_category`` we fall back to the category's
    canonical name; if it did not pick a ``sheet_group`` we fall back
    to the empty string. This keeps any partial resolution ("tag
    rewrote only the envelope column") instead of discarding both
    columns when one side stays wildcard.

    Returns ``None`` only when ``category_id`` itself is not in the
    catalog — that is the one condition the caller cannot recover
    from and must translate into a "poison this job" signal.

    NOTE: ``sheet_mapping.resolve_projection`` implements the same
    "first non-``*`` wins per column" rule over pure ``MapRow``
    objects; the two helpers intentionally stay separate so this
    function can run directly against the DB without materializing
    every row. Any change to the matching rule must be mirrored in
    both places.
    """
    expense_tag_set = {int(t) for t in tag_ids}
    category_fallback = get_category_name(con, category_id)
    if category_fallback is None:
        return None
    candidates = fetchall_as(
        LoggingProjectionCandidateRow,
        con,
        load_sql("logging_projection.sql"),
        [category_id],
    )

    resolved_category: str | None = None
    resolved_group: str | None = None
    for cand in candidates:
        if cand.event_id is not None and cand.event_id != event_id:
            continue
        required_tags = {int(t) for t in cand.tag_ids}
        if not required_tags.issubset(expense_tag_set):
            continue
        if resolved_category is None and cand.sheet_category != _PROJECTION_WILDCARD:
            resolved_category = cand.sheet_category
        if resolved_group is None and cand.sheet_group != _PROJECTION_WILDCARD:
            resolved_group = cand.sheet_group
        if resolved_category is not None and resolved_group is not None:
            break

    return (
        resolved_category if resolved_category is not None else category_fallback,
        resolved_group if resolved_group is not None else "",
    )


# ---------------------------------------------------------------------------
# Expense lookup and insert
# ---------------------------------------------------------------------------


def lookup_existing_expense(
    client_expense_id: str,
    *,
    con: duckdb.DuckDBPyConnection | None = None,
) -> ExistingExpenseRow | None:
    """Look up a stored expense by client_expense_id.

    When ``con`` is provided, run the SELECT on the caller's cursor —
    this lets ``POST /api/expenses`` do the active-category /
    idempotent-replay check on the same cursor it's about to pass to
    ``insert_expense``, instead of opening a second short-lived cursor
    on every write that hits an inactive category.

    When ``con`` is omitted, we fall back to opening and closing a
    fresh cursor, for callers (tests, debugging utilities) that don't
    have one handy.

    **Snapshot invariant.** For the two branches to return equivalent
    data, the caller-supplied cursor must be in auto-commit mode (no
    open ``BEGIN``). The fresh-cursor branch always sees only
    committed rows; the ``con=`` branch sees the caller's snapshot,
    which inside an open transaction includes that transaction's own
    uncommitted writes. Today's only ``con=`` caller
    (``api.expenses._resolve_category_for_write``) is invoked before
    ``insert_expense`` opens its ``BEGIN``, so the invariant holds.
    Any future caller that threads this cursor through an already-
    open transaction must understand the divergence or explicitly
    drop to auto-commit first.
    """
    if con is not None:
        return fetchone_as(
            ExistingExpenseRow,
            con,
            load_sql("get_existing_expense.sql"),
            [client_expense_id],
        )
    own_con = get_connection()
    try:
        return fetchone_as(
            ExistingExpenseRow,
            own_con,
            load_sql("get_existing_expense.sql"),
            [client_expense_id],
        )
    finally:
        own_con.close()


InsertExpenseResult = Literal["created", "duplicate", "conflict"]


#: Names of the stored/incoming tuple components, kept 1:1 with the
#: ``stored = (...)`` / ``incoming = (...)`` order inside
#: ``insert_expense``. Used by ``_format_expense_diff`` to produce
#: human-readable conflict diffs for the 409 response body and server
#: logs.
_EXPENSE_DIFF_FIELDS: tuple[str, ...] = (
    "amount",
    "amount_original",
    "currency_original",
    "category_id",
    "event_id",
    "comment",
    "datetime",
    "sheet_category",
    "sheet_group",
    "tag_ids",
)


def _format_expense_diff(stored: tuple, incoming: tuple) -> str:
    """Return a compact list of the columns that differ between stored + incoming.

    The 409 caller surfaces the result to the PWA so an operator
    replaying an offline queue can tell the real-conflict case
    ("different amount / comment") from the narrow race on
    ``events.auto_tags`` edits ("different tag_ids only"). Values are
    repr'd to stay grep-able.
    """
    diffs: list[str] = []
    for field, a, b in zip(_EXPENSE_DIFF_FIELDS, stored, incoming, strict=True):
        if a != b:
            diffs.append(f"{field}: stored={a!r} incoming={b!r}")
    return "; ".join(diffs) if diffs else "(no field difference observed)"


def describe_expense_conflict(  # noqa: PLR0913
    con: duckdb.DuckDBPyConnection,
    *,
    client_expense_id: str,
    expense_datetime: datetime,
    amount: float,
    amount_original: float,
    currency_original: str,
    category_id: int,
    event_id: int | None,
    comment: str,
    sheet_category: str | None,
    sheet_group: str | None,
    tag_ids: list[int],
) -> str | None:
    """Re-run the stored-vs-incoming compare and return a human-readable diff.

    Called from ``api/expenses.py`` on the conflict path so the 409
    body can tell the operator which fields changed (most often only
    ``tag_ids`` when an ``events.auto_tags`` edit landed mid-retry).
    Returns ``None`` when the stored row has vanished between the
    conflict signal and this lookup — a degenerate state that should
    not happen in practice but we guard against rather than crash.
    """
    existing = fetchone_as(
        ExistingExpenseRow,
        con,
        load_sql("get_existing_expense.sql"),
        [client_expense_id],
    )
    if existing is None:
        return None
    existing_tag_ids = sorted(
        int(r[0])
        for r in con.execute(
            "SELECT tag_id FROM expense_tags WHERE expense_id = ?",
            [existing.id],
        ).fetchall()
    )
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
        sorted(int(t) for t in tag_ids),
    )
    return _format_expense_diff(stored, incoming)


def insert_expense(  # noqa: PLR0913, C901, PLR0912, PLR0915
    con: duckdb.DuckDBPyConnection,
    *,
    client_expense_id: str | None,
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
    enqueue_logging: bool = True,
) -> InsertExpenseResult:
    """Insert an expense + tags + queue row in one transaction.

    Returns 'created', 'duplicate', or 'conflict'. Safe under
    concurrent callers sharing the same ``client_expense_id``: the
    race is resolved deterministically by the UNIQUE constraint, and
    the loser falls through to the compare path against the winner's
    committed row.
    """
    if (sheet_category is None) != (sheet_group is None):
        msg = (
            "sheet_category and sheet_group must be both NULL (runtime row) "
            "or both non-NULL (bootstrap-imported provenance row)"
        )
        raise ValueError(msg)

    tag_ids = tag_ids or []
    incoming_tag_ids = sorted(int(t) for t in tag_ids)
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

    con.execute("BEGIN")
    tx_active = True
    try:
        if not con.execute(
            "SELECT 1 FROM categories WHERE id = ?",
            [category_id],
        ).fetchone():
            raise ValueError(f"category_id {category_id} not found in categories")
        if (
            event_id is not None
            and not con.execute(
                "SELECT 1 FROM events WHERE id = ?",
                [event_id],
            ).fetchone()
        ):
            raise ValueError(f"event_id {event_id} not found in events")
        for tid in tag_ids:
            if not con.execute(
                "SELECT 1 FROM tags WHERE id = ?",
                [tid],
            ).fetchone():
                raise ValueError(f"tag_id {tid} not found in tags")

        # Under DuckDB's optimistic MVCC, ``ON CONFLICT DO NOTHING``
        # only silently absorbs conflicts with rows that are already
        # *committed* at statement time. A concurrent in-flight writer
        # holding the same UNIQUE key instead surfaces immediately as
        # a ``ConstraintException`` here; if that in-flight writer
        # commits *after* our INSERT statement but *before* our COMMIT,
        # our commit itself fails (as a ``TransactionException`` or
        # a ``ConstraintException`` — DuckDB picks the class based on
        # when it notices the conflict) with a message that wraps the
        # same duplicate-key violation. Both cases reduce to "the
        # winner is committed, we should compare against their row" —
        # fall through to the compare-outside-tx path.
        #
        # Important: DuckDB marks the TX as aborted after either
        # failure but does **not** clear the cursor state. Any
        # subsequent statement on the same cursor rejects with
        # ``TransactionContext Error: Current transaction is aborted
        # (please ROLLBACK)`` until we issue an explicit ROLLBACK.
        # The race-recovery branches below do exactly that via
        # ``best_effort_rollback`` before falling through to the
        # compare path, which is why those ROLLBACKs look redundant
        # but are load-bearing.
        inserted: tuple | None
        try:
            inserted = con.execute(
                load_sql("insert_expense.sql"),
                [
                    client_expense_id,
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
        except _DUCKDB_RACE_EXCS as exc:
            if not _is_unique_violation_of_client_expense_id(exc):
                raise
            # Same aborted-TX cleanup as the COMMIT branch below:
            # DuckDB leaves the cursor in an aborted state after a
            # failed INSERT, and every subsequent statement on it
            # rejects with "Current transaction is aborted (please
            # ROLLBACK)" until we issue an explicit ROLLBACK.
            best_effort_rollback(
                con,
                context=(
                    "insert_expense race-recovery at INSERT "
                    f"(client_expense_id={client_expense_id!r})"
                ),
            )
            inserted = None
            tx_active = False

        if inserted is not None:
            expense_pk = int(inserted[0])
            for tid in tag_ids:
                # Narrow the conflict target to the composite PK.
                # Bare ``ON CONFLICT`` would work identically today
                # (only one conflict target exists), but would silently
                # absorb any future UNIQUE added to ``expense_tags``
                # with no recovery net to notice — unlike
                # ``insert_expense.sql`` whose compare-against-winner
                # path would at least catch a mis-classified
                # duplicate. Keep it scoped so an unexpected UNIQUE
                # on, say, ``(expense_id, sort_order)`` raises cleanly.
                con.execute(
                    "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)"
                    " ON CONFLICT (expense_id, tag_id) DO NOTHING",
                    [expense_pk, tid],
                )
            if enqueue_logging:
                # Same reasoning: narrow to the PK target explicitly
                # so any future UNIQUE on ``sheet_logging_jobs``
                # (e.g. per-drain-run dedup) raises instead of being
                # silently swallowed by an over-broad ON CONFLICT.
                con.execute(
                    "INSERT INTO sheet_logging_jobs (expense_id, status)"
                    " VALUES (?, 'pending') ON CONFLICT (expense_id) DO NOTHING",
                    [expense_pk],
                )
            try:
                con.execute("COMMIT")
            except _DUCKDB_RACE_EXCS as exc:
                if not _is_unique_violation_of_client_expense_id(exc):
                    raise
                # DuckDB marks the TX as aborted on a failed commit;
                # subsequent statements on the same cursor would reject
                # with "Current transaction is aborted (please
                # ROLLBACK)". An explicit ROLLBACK drops us back to
                # auto-commit so the compare path below can run.
                best_effort_rollback(
                    con,
                    context=(
                        f"insert_expense race-recovery (client_expense_id={client_expense_id!r})"
                    ),
                )
                tx_active = False
            else:
                return "created"
        elif tx_active:
            # Classic ON CONFLICT DO NOTHING hit: the winner committed
            # before our INSERT ran, so we never allocated a PK but
            # our validation SELECTs are still sitting in an open TX.
            # Close it cleanly before running the compare in
            # auto-commit — keeping it open has no benefit and makes
            # the cleanup at the bottom of this function harder to
            # reason about.
            con.execute("ROLLBACK")
            tx_active = False

        # Compare path — runs in auto-commit against the committed
        # winner row (whichever leg we arrived on). client_expense_id
        # is guaranteed non-None here: NULL rows can't trigger either
        # branch because UNIQUE allows multiple NULLs, so a NULL
        # incoming UUID always ends up in the happy-path above.
        existing = fetchone_as(
            ExistingExpenseRow,
            con,
            load_sql("get_existing_expense.sql"),
            [client_expense_id],
        )
        if existing is None:
            # Defensive: if the winner's commit got rolled back between
            # our failed COMMIT and this SELECT, treat the condition
            # as an unrecoverable internal error with a loud,
            # traceable message (``assert`` is stripped by ``python -O``
            # and would swallow this class of bug in a production
            # build).
            msg = (
                f"insert_expense: client_expense_id={client_expense_id!r} "
                "disappeared between ON CONFLICT/race recovery and the "
                "compare SELECT — concurrent writer rolled back after "
                "its commit? DB state is inconsistent with our "
                "assumptions."
            )
            raise RuntimeError(msg)

        existing_tag_ids = sorted(
            int(r[0])
            for r in con.execute(
                "SELECT tag_id FROM expense_tags WHERE expense_id = ?",
                [existing.id],
            ).fetchall()
        )

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

        if stored == incoming:
            return "duplicate"
        logger.info(
            "insert_expense conflict for client_expense_id=%r: diff=%s",
            client_expense_id,
            _format_expense_diff(stored, incoming),
        )
        return "conflict"

    except Exception:
        if tx_active:
            best_effort_rollback(
                con,
                context=f"insert_expense(client_expense_id={client_expense_id!r})",
            )
        raise


def get_expense_tags(con: duckdb.DuckDBPyConnection, expense_id: int) -> list[int]:
    """Return the tag_ids attached to an expense, sorted ascending."""
    rows = con.execute(
        "SELECT tag_id FROM expense_tags WHERE expense_id = ? ORDER BY tag_id",
        [expense_id],
    ).fetchall()
    return [int(r[0]) for r in rows]


def get_expense_by_id(con: duckdb.DuckDBPyConnection, expense_id: int) -> ExpenseRow | None:
    """Read a stored expense row by integer PK."""
    return fetchone_as(
        ExpenseRow,
        con,
        "SELECT id, client_expense_id, datetime, amount, amount_original,"
        " currency_original, category_id, event_id, comment,"
        " sheet_category, sheet_group"
        " FROM expenses WHERE id = ?",
        [expense_id],
    )


# ---------------------------------------------------------------------------
# sheet_logging_jobs queue helpers (integer PK based)
# ---------------------------------------------------------------------------


def list_logging_jobs(
    con: duckdb.DuckDBPyConnection,
    *,
    now: datetime | None = None,
    stale_before: datetime | None = None,
) -> list[int]:
    """Return expense_ids the drain should attempt this pass.

    That is: rows in ``pending`` plus rows in ``in_progress`` whose
    claim is older than ``stale_before`` (orphaned claims from a
    previous worker that crashed mid-drain). Fresh ``in_progress``
    rows are excluded because ``claim_logging_job`` would just reject
    them — listing them here would burn one BEGIN/COMMIT round-trip
    per row per drain iteration for no reason. Poisoned rows are
    always excluded.
    """
    if now is None:
        now = datetime.now()
    if stale_before is None:
        stale_before = now - default_claim_stale_timeout()
    rows = con.execute(
        "SELECT expense_id FROM sheet_logging_jobs"
        " WHERE status = 'pending'"
        "    OR (status = 'in_progress' AND claimed_at < ?)"
        " ORDER BY expense_id",
        [stale_before],
    ).fetchall()
    return [int(r[0]) for r in rows]


def count_logging_jobs(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT count(*) FROM sheet_logging_jobs").fetchone()
    return int(row[0]) if row else 0


def claim_logging_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: int,
    *,
    claim_token: str | None = None,
    now: datetime | None = None,
    stale_before: datetime | None = None,
) -> str | None:
    """Atomically claim a queue row. Returns the claim_token on success, None otherwise."""
    if claim_token is None:
        claim_token = uuid.uuid4().hex
    if now is None:
        now = datetime.now()
    if stale_before is None:
        stale_before = now - default_claim_stale_timeout()

    con.execute("BEGIN")
    try:
        row = con.execute(
            "SELECT status, claim_token, claimed_at FROM sheet_logging_jobs WHERE expense_id = ?",
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
            "UPDATE sheet_logging_jobs SET status = 'in_progress',"
            " claim_token = ?, claimed_at = ? WHERE expense_id = ?",
            [claim_token, now, expense_id],
        )
        con.execute("COMMIT")
        return claim_token
    except duckdb.TransactionException:
        best_effort_rollback(con, context=f"claim_logging_job({expense_id}) txn conflict")
        logger.debug("Transaction conflict claiming %s; another worker won", expense_id)
        return None
    except Exception:
        best_effort_rollback(con, context=f"claim_logging_job({expense_id}) generic error")
        raise


def release_logging_claim(
    con: duckdb.DuckDBPyConnection,
    expense_id: int,
    claim_token: str,
) -> bool:
    rows = con.execute(
        "UPDATE sheet_logging_jobs SET status = 'pending', claim_token = NULL, claimed_at = NULL"
        " WHERE expense_id = ? AND claim_token = ? RETURNING expense_id",
        [expense_id, claim_token],
    ).fetchall()
    return len(rows) > 0


def poison_logging_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: int,
    error: str,
) -> None:
    """Mark a queue row as poisoned with an error reason."""
    con.execute(
        "UPDATE sheet_logging_jobs SET status = 'poisoned', last_error = ? WHERE expense_id = ?",
        [error, expense_id],
    )


def _delete_logging_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: int,
    *,
    claim_token: str | None,
) -> bool:
    if claim_token is None:
        rows = con.execute(
            "DELETE FROM sheet_logging_jobs WHERE expense_id = ? RETURNING expense_id",
            [expense_id],
        ).fetchall()
    else:
        rows = con.execute(
            "DELETE FROM sheet_logging_jobs WHERE expense_id = ? AND claim_token = ?"
            " RETURNING expense_id",
            [expense_id, claim_token],
        ).fetchall()
    return len(rows) > 0


def clear_logging_job(
    con: duckdb.DuckDBPyConnection,
    expense_id: int,
    claim_token: str,
) -> bool:
    return _delete_logging_job(con, expense_id, claim_token=claim_token)


def force_clear_logging_job(con: duckdb.DuckDBPyConnection, expense_id: int) -> bool:
    return _delete_logging_job(con, expense_id, claim_token=None)


def get_month_expenses(
    con: duckdb.DuckDBPyConnection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    return fetchall_as(ExpenseRow, con, load_sql("get_month_expenses.sql"), [year, month])
