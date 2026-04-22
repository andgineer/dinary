"""Storage repository: single data/dinary.db SQLite file for catalog + ledger.

Connection model: each ``get_connection()`` call opens a fresh sqlite3
connection against the ``DB_PATH`` file with our standard PRAGMAs
applied. Callers are responsible for closing it in a ``try/finally``.
``init_db()`` runs migrations and reconciles the accounting-currency
anchor before any caller takes a connection.
"""

import contextlib
import dataclasses
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

from dinary.config import settings
from dinary.services import db_migrations
from dinary.services import sqlite_types as _sqlite_types
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


def best_effort_rollback(con: sqlite3.Connection, *, context: str) -> None:
    """Issue ROLLBACK without letting a failed rollback mask the original error."""
    try:
        con.execute("ROLLBACK")
    except Exception:
        logger.exception("Best-effort ROLLBACK failed (context: %s)", context)


def _is_unique_violation_of_client_expense_id(exc: BaseException) -> bool:
    """True iff ``exc`` is a UNIQUE violation on ``expenses.client_expense_id``.

    Under SQLite WAL the only way to reach this branch is via a
    concurrent transaction that committed ahead of ours *without*
    ``ON CONFLICT DO NOTHING`` absorbing the duplicate. SQLite itself
    absorbs the common "winner already committed" case silently (the
    RETURNING clause returns an empty result set), so the loser
    rarely reaches here under ``insert_expense.sql``. The branch is
    retained as a defensive backstop so that a future writer using
    bare ``INSERT`` (without ON CONFLICT) on a UNIQUE ``client_expense_id``
    column still lands in the race-recovery compare path instead of
    bubbling an ``IntegrityError`` to the API layer.

    SQLite's diagnostic for a UNIQUE violation on
    ``expenses.client_expense_id`` is
    ``"UNIQUE constraint failed: expenses.client_expense_id"``. We
    match on the qualified column name so an unrelated UNIQUE added
    to any other table would raise cleanly rather than be silently
    laundered through this classifier. Foreign-key violations raise
    ``IntegrityError`` with a ``"FOREIGN KEY constraint failed"``
    message and are explicitly excluded.
    """
    message = str(exc).lower()
    if "foreign key" in message:
        return False
    return "unique constraint failed" in message and "expenses.client_expense_id" in message


# Exception classes SQLite may raise when an ``ON CONFLICT DO NOTHING``
# can't silently absorb a duplicate (e.g. a future writer that issues
# a bare ``INSERT``). Kept as a tuple so adding sibling classes later
# (e.g. ``OperationalError`` for a retry ladder) stays a one-line edit.
_RACE_EXCS: tuple[type[sqlite3.Error], ...] = (sqlite3.IntegrityError,)


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

    ``tag_ids_json`` arrives as a JSON-encoded string from
    ``json_group_array`` in ``logging_projection.sql``; the Python
    consumer parses it once per row. The field is named with the
    ``_json`` suffix so the raw-string shape is obvious at call
    sites and does not get confused with a decoded ``list[int]``.
    """

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

    Must be called once per process before ``get_connection()``. The FastAPI
    lifespan in ``dinary.main`` invokes it on startup; ``invoke`` tasks and
    tests do so explicitly (see ``tests/conftest.py::_reset_db_connection``).
    """
    ensure_data_dir()
    db_migrations.migrate_db(_get_db_path())
    con = _sqlite_types.connect(str(_get_db_path()))
    try:
        _reconcile_accounting_currency(con)
    finally:
        con.close()


def _reconcile_accounting_currency(con: sqlite3.Connection) -> None:
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
                "Fresh data/dinary.db and settings.accounting_currency is empty; "
                "refusing to seed an unknown accounting currency. Set "
                "DINARY_ACCOUNTING_CURRENCY in .deploy/.env to a valid ISO-4217 "
                "code (e.g. EUR) for the first deploy; subsequent runs can omit "
                "it and the value will be read back from app_metadata."
            )
            raise RuntimeError(msg)
        # Codebase-wide rule is "writers take ``BEGIN IMMEDIATE``", and the
        # first-boot anchor is no exception: wrapping the seed INSERT in an
        # explicit write transaction lets ``busy_timeout`` absorb any
        # accidentally-concurrent writer (e.g. a migration runner still
        # holding the write lock) instead of surfacing ``SQLITE_BUSY`` at
        # COMMIT. At runtime ``init_db`` is invoked once per process from
        # the FastAPI lifespan, so contention is not expected — the
        # ``BEGIN IMMEDIATE`` is pure belt-and-braces.
        con.execute("BEGIN IMMEDIATE")
        try:
            con.execute(
                "INSERT INTO app_metadata (key, value) VALUES ('accounting_currency', ?)",
                [desired],
            )
        except BaseException:
            # Shield the ROLLBACK: if SQLite has already auto-rolled back
            # (or the connection is otherwise unable to roll back), letting
            # the ROLLBACK exception bubble would mask the original INSERT
            # error the operator actually needs to see. Same idiom as
            # ``best_effort_rollback`` below and ``SQLiteBackend.rollback``
            # in ``db_migrations``.
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

    **Concurrency model.** SQLite in WAL mode supports many readers
    and one writer per file; multiple processes or threads can share
    the file safely. Each call opens its own file handle with
    ``PRAGMA busy_timeout`` configured, so writers serialize on
    ``BEGIN IMMEDIATE`` / first-write and readers never block on
    them. Each caller gets an independent transaction context
    without a process-wide singleton.

    **UNIQUE races.** ``insert_expense.sql`` uses
    ``ON CONFLICT (client_expense_id) DO NOTHING RETURNING id``.
    Under SQLite, if the winner has already committed, the RETURNING
    clause yields no rows and we fall through to the compare-outside-tx
    path naturally — no exception to handle. If a concurrent writer is
    still in-flight, the loser either blocks up to ``busy_timeout`` or
    (if it timed out) raises ``OperationalError`` which
    ``insert_expense`` lets propagate. Any new writer module that
    takes a cursor from here and uses ``ON CONFLICT`` on a
    user-supplied UNIQUE key inherits this behaviour automatically;
    writers that skip ``ON CONFLICT`` must apply the same
    compare-path recovery pattern as ``insert_expense`` does via
    ``_is_unique_violation_of_client_expense_id``.

    **Lifecycle.** ``get_connection()`` does not run migrations.
    Callers that need a schema-guaranteed DB (``dinary.main`` lifespan,
    ``invoke migrate``, tests) must call ``init_db()`` first. The
    returned connection **must** be closed by the caller
    (``try ... finally: con.close()``).
    """
    return _sqlite_types.connect(str(_get_db_path()))


def close_connection() -> None:
    """No-op tear-down hook for fixtures.

    Each ``get_connection`` call opens a fresh sqlite3 handle that the
    caller owns and closes itself, so there is no process-wide handle
    to release here. The function is kept so fixtures have a stable
    hook if a connection-pool-style cache is ever introduced.
    """


# ---------------------------------------------------------------------------
# Catalog queries
# ---------------------------------------------------------------------------


def list_categories(con: sqlite3.Connection) -> list[CategoryListRow]:
    """Return active categories with active group info, ordered by group sort then name."""
    return fetchall_as(CategoryListRow, con, load_sql("list_categories.sql"))


def get_catalog_version(con: sqlite3.Connection) -> int:
    row = con.execute(
        "SELECT value FROM app_metadata WHERE key = 'catalog_version'",
    ).fetchone()
    if row is None:
        msg = "app_metadata 'catalog_version' key is missing"
        raise RuntimeError(msg)
    return int(row[0])


def set_catalog_version(con: sqlite3.Connection, value: int) -> None:
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


def get_category_name(con: sqlite3.Connection, category_id: int) -> str | None:
    row = con.execute(
        "SELECT name FROM categories WHERE id = ?",
        [category_id],
    ).fetchone()
    return str(row[0]) if row else None


# ---------------------------------------------------------------------------
# Sheet mapping resolution (import path)
# ---------------------------------------------------------------------------


def resolve_mapping(
    con: sqlite3.Connection,
    category: str,
    group: str,
) -> MappingRow | None:
    return fetchone_as(MappingRow, con, load_sql("resolve_mapping.sql"), [category, group])


def resolve_mapping_for_year(
    con: sqlite3.Connection,
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
    con: sqlite3.Connection,
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
    con: sqlite3.Connection,
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
        required_tags = {int(t) for t in json.loads(cand.tag_ids_json)}
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
    con: sqlite3.Connection | None = None,
) -> ExistingExpenseRow | None:
    """Look up a stored expense by client_expense_id.

    When ``con`` is provided, run the SELECT on the caller's connection —
    this lets ``POST /api/expenses`` do the active-category /
    idempotent-replay check on the same connection it's about to pass to
    ``insert_expense``, instead of opening a second short-lived
    connection on every write that hits an inactive category.

    When ``con`` is omitted, we fall back to opening and closing a
    fresh connection, for callers (tests, debugging utilities) that don't
    have one handy.

    **Snapshot invariant.** For the two branches to return equivalent
    data, the caller-supplied connection must be in auto-commit mode
    (no open ``BEGIN``). The fresh-connection branch always sees only
    committed rows; the ``con=`` branch sees the caller's snapshot,
    which inside an open transaction includes that transaction's own
    uncommitted writes. Today's only ``con=`` caller
    (``api.expenses._resolve_category_for_write``) is invoked before
    ``insert_expense`` opens its ``BEGIN``, so the invariant holds.
    Any future caller that threads this connection through an already-
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


def _to_decimal(value: float | Decimal) -> Decimal:
    """Coerce amount-shaped inputs to ``Decimal`` for consistent storage.

    ``dinary.services.sqlite_types`` registers a ``Decimal`` adapter
    that always stores ``Decimal`` as the ``str(value)`` TEXT form,
    and a matching ``DECIMAL`` converter that parses it back. Handing
    a ``float`` to ``con.execute`` would bypass that adapter and
    persist the column as SQLite's native REAL instead, so the same
    ``DECIMAL(p,s)`` column would end up with mixed REAL/TEXT values
    and the converter would either lose precision or choke on
    readback. Coerce here so every bind parameter that lands in a
    ``DECIMAL`` column travels through the registered adapter.
    """
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def describe_expense_conflict(  # noqa: PLR0913
    con: sqlite3.Connection,
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
        _to_decimal(amount),
        _to_decimal(amount_original),
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
    con: sqlite3.Connection,
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
    amount_dec = _to_decimal(amount)
    amount_original_dec = _to_decimal(amount_original)
    incoming = (
        amount_dec,
        amount_original_dec,
        currency_original,
        category_id,
        event_id,
        comment,
        expense_datetime,
        sheet_category,
        sheet_group,
        incoming_tag_ids,
    )

    # ``BEGIN IMMEDIATE`` makes this transaction a writer from the start
    # (upgrades the SHARED lock to RESERVED), which forces any other
    # concurrent writer to serialize on the SQLite file lock instead of
    # deferring the contention to the first UPDATE/INSERT. That lets
    # the ``busy_timeout`` we set at connect time actually kick in
    # here (the default "DEFERRED" transaction would upgrade lazily
    # at the first write statement, which can give a BUSY on commit
    # that is harder to recover cleanly).
    con.execute("BEGIN IMMEDIATE")
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

        # Under SQLite's single-writer model, ``ON CONFLICT DO NOTHING``
        # silently absorbs conflicts with rows already committed at
        # statement time; the RETURNING clause returns an empty result
        # set in that case and we fall through to the compare-outside-
        # tx path. A concurrent in-flight writer holding the same
        # UNIQUE key either blocks us (via busy_timeout) or raises
        # ``OperationalError`` ("database is locked") — those are
        # NOT duplicate-key races and must propagate.
        #
        # The ``_RACE_EXCS`` branch below is a defensive backstop for
        # any future writer that skips ``ON CONFLICT`` and bubbles an
        # IntegrityError. We clean up the aborted TX and fall through
        # to the compare-against-winner path.
        inserted: tuple | None
        try:
            inserted = con.execute(
                load_sql("insert_expense.sql"),
                [
                    client_expense_id,
                    expense_datetime,
                    amount_dec,
                    amount_original_dec,
                    currency_original,
                    category_id,
                    event_id,
                    comment,
                    sheet_category,
                    sheet_group,
                ],
            ).fetchone()
        except _RACE_EXCS as exc:
            if not _is_unique_violation_of_client_expense_id(exc):
                raise
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
                con.execute(
                    "INSERT INTO expense_tags (expense_id, tag_id) VALUES (?, ?)"
                    " ON CONFLICT (expense_id, tag_id) DO NOTHING",
                    [expense_pk, tid],
                )
            if enqueue_logging:
                con.execute(
                    "INSERT INTO sheet_logging_jobs (expense_id, status)"
                    " VALUES (?, 'pending') ON CONFLICT (expense_id) DO NOTHING",
                    [expense_pk],
                )
            try:
                con.execute("COMMIT")
            except _RACE_EXCS as exc:
                if not _is_unique_violation_of_client_expense_id(exc):
                    raise
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
            # ON CONFLICT DO NOTHING hit on an already-committed winner:
            # close our still-open read transaction cleanly before
            # running the compare in auto-commit.
            con.execute("ROLLBACK")
            tx_active = False

        # Compare path — runs in auto-commit against the committed
        # winner row. client_expense_id is guaranteed non-None here:
        # NULL rows can't trigger either branch because UNIQUE allows
        # multiple NULLs, so a NULL incoming UUID always ends up in
        # the happy-path above.
        existing = fetchone_as(
            ExistingExpenseRow,
            con,
            load_sql("get_existing_expense.sql"),
            [client_expense_id],
        )
        if existing is None:
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


def get_expense_tags(con: sqlite3.Connection, expense_id: int) -> list[int]:
    """Return the tag_ids attached to an expense, sorted ascending."""
    rows = con.execute(
        "SELECT tag_id FROM expense_tags WHERE expense_id = ? ORDER BY tag_id",
        [expense_id],
    ).fetchall()
    return [int(r[0]) for r in rows]


def get_expense_by_id(con: sqlite3.Connection, expense_id: int) -> ExpenseRow | None:
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
    con: sqlite3.Connection,
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


def count_logging_jobs(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT count(*) FROM sheet_logging_jobs").fetchone()
    return int(row[0]) if row else 0


def claim_logging_job(
    con: sqlite3.Connection,
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

    # ``BEGIN IMMEDIATE`` serializes multiple drain workers on the
    # SQLite write lock from the start of the txn, so the race
    # between SELECT and UPDATE can't be won by two workers at once.
    # A contending worker either waits up to ``busy_timeout`` or
    # surfaces as ``OperationalError`` ("database is locked"), which
    # we treat as "another worker won".
    try:
        con.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        logger.debug("BEGIN IMMEDIATE lost the write-lock race claiming %s", expense_id)
        return None
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
    except sqlite3.OperationalError:
        best_effort_rollback(con, context=f"claim_logging_job({expense_id}) lock conflict")
        logger.debug("Lock conflict claiming %s; another worker won", expense_id)
        return None
    except Exception:
        best_effort_rollback(con, context=f"claim_logging_job({expense_id}) generic error")
        raise


def release_logging_claim(
    con: sqlite3.Connection,
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
    con: sqlite3.Connection,
    expense_id: int,
    error: str,
) -> None:
    """Mark a queue row as poisoned with an error reason."""
    con.execute(
        "UPDATE sheet_logging_jobs SET status = 'poisoned', last_error = ? WHERE expense_id = ?",
        [error, expense_id],
    )


def _delete_logging_job(
    con: sqlite3.Connection,
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
    con: sqlite3.Connection,
    expense_id: int,
    claim_token: str,
) -> bool:
    return _delete_logging_job(con, expense_id, claim_token=claim_token)


def force_clear_logging_job(con: sqlite3.Connection, expense_id: int) -> bool:
    return _delete_logging_job(con, expense_id, claim_token=None)


def get_month_expenses(
    con: sqlite3.Connection,
    year: int,
    month: int,
) -> list[ExpenseRow]:
    return fetchall_as(ExpenseRow, con, load_sql("get_month_expenses.sql"), [year, month])
