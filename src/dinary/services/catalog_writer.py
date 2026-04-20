"""Admin-API write path for the catalog tables.

Every mutation that flows through ``dinary.api.admin_catalog`` lands
here. Each public method:

1. Opens one DuckDB transaction.
2. Computes ``before_hash`` — sha256 of a canonical state tuple over
   all four catalog tables (``category_groups`` / ``categories`` /
   ``events`` / ``tags``).
3. Applies the mutation.
4. Computes ``after_hash``.
5. If the hashes differ, increments ``app_metadata.catalog_version``.
6. Commits.

This guarantees the invariant: any observable structural change
(add / rename / date-edit / is_active flip / group move) bumps
``catalog_version``; a no-op rewrite does not. PWA clients observe
the bump on the next ``POST /api/expenses`` response and refresh the
cached catalog in the background.

Integrity rules enforced here (never at SQL level):

* Cannot soft-delete a category / event / tag referenced by any
  ``expenses`` row — surfaces as ``CatalogInUseError`` with usage
  count so admin API can translate to 409.
* Cannot rename to a name already in use (409).
* Cannot set a category's ``group_id`` to an inactive or missing
  group (422).
* ``date_from <= date_to`` on events (422).

Reactivate semantics
--------------------

``add_*`` on a name that already maps to an existing (possibly
inactive) row never overwrites the existing row's optional columns.
Behaviour:

* Active match + no observable change → no-op, ``status="noop"``,
  no version bump, returns the existing id.
* Inactive match → flip ``is_active=TRUE`` only;
  ``status="reactivated"``, version bumps, returns the existing id.
* New name → INSERT; ``status="created"``, version bumps, returns the
  new id.

Rationale: the "+ Новый" admin flow in the PWA should never silently
clobber ``sheet_name`` / ``sheet_group`` / ``date_from`` / ``date_to``
/ ``auto_attach_enabled`` / ``sort_order`` on an inactive row that
the operator chose to reactivate by re-typing its name. To change
any of those, the caller uses ``edit_*`` (admin PATCH) instead.
Exception: ``add_category`` accepts a ``group_id`` which *is* applied
on reactivate, because the caller's group selection is part of the
"where am I putting this category" intent of the add action.

Relationship to the seed path
-----------------------------

``seed_config.rebuild_config_from_sheets`` (the ``inv import-catalog``
entry point) does **not** flow through this module. It wraps its
whole catalog rebuild in a single long transaction and calls
``seed_config._bump_catalog_version`` once at the end, which
satisfies the "catalog change bumps version" invariant via a
different path. The two write paths are kept separate because
``catalog_writer`` opens per-mutation transactions that cannot nest
inside the seed's outer ``BEGIN/COMMIT``. A future unification would
require restructuring the seed to commit per entity; that refactor is
out of scope here. Until then, both paths funnel version writes
through ``duckdb_repo.set_catalog_version`` so any future audit hook
can intercept them uniformly.
"""

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

import duckdb

from dinary.services import duckdb_repo

logger = logging.getLogger(__name__)


CatalogKind = Literal["category_group", "category", "event", "tag"]

AddStatus = Literal["created", "reactivated", "noop"]


@dataclass(frozen=True, slots=True)
class AddResult:
    """Return value of ``add_group`` / ``add_category`` / ``add_event`` / ``add_tag``.

    ``id`` is the row id (existing or new). ``status`` distinguishes
    between a brand-new INSERT, a reactivate-in-place, and a fully
    silent no-op (active row with matching fields). Admin-API callers
    propagate ``status`` to the PWA response so the UI can tell the
    user "reactivated existing" vs "created new".
    """

    id: int
    status: AddStatus


class CatalogWriteError(Exception):
    """Base class for catalog writer errors raised to API callers."""

    http_status: int = 422

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        if http_status is not None:
            self.http_status = http_status


class CatalogInUseError(CatalogWriteError):
    """Soft-delete blocked because the row is still referenced by expenses."""

    http_status = 409

    def __init__(self, kind: CatalogKind, row_id: int, usage_count: int) -> None:
        super().__init__(
            f"{kind} id={row_id} is still referenced by {usage_count} expense row(s); "
            "rename it instead of deactivating, or retire the referencing expenses first",
        )
        self.kind = kind
        self.row_id = row_id
        self.usage_count = usage_count


class CatalogNotFoundError(CatalogWriteError):
    http_status = 404


class CatalogConflictError(CatalogWriteError):
    http_status = 409


# ---------------------------------------------------------------------------
# Canonical state + diff-driven version bump
# ---------------------------------------------------------------------------


def hash_catalog_state(con: duckdb.DuckDBPyConnection) -> str:
    """Return a hex sha256 over the canonical catalog state.

    Public re-export of ``_hash_state`` so write paths outside this
    module (notably ``seed_config.rebuild_config_from_sheets``) can
    gate their ``catalog_version`` bump on the same invariant this
    module enforces: version only changes when the observable catalog
    state does. Using the same helper in both paths guarantees a
    single definition of "observable".
    """
    return _hash_state(con)


def _canonical_state(con: duckdb.DuckDBPyConnection) -> bytes:
    """Serialise the full catalog to a deterministic byte string.

    The hash of this buffer before and after the mutation tells
    ``_commit_with_bump`` whether anything observable actually
    changed. "Observable" is the set of columns any consumer
    (``GET /api/catalog``, seed validation, admin PATCH) can see:
    ``name``, ``is_active``, ``group_id`` (categories),
    ``date_from/date_to/auto_attach_enabled`` (events),
    ``sort_order`` (category_groups), ``sheet_name/sheet_group``
    (categories). Primary keys (``id``) are included so a
    retire-and-recreate under the same name registers as a change.

    Ordered by ``id`` so reordering a response body doesn't leak
    into the hash.
    """
    parts: list[str] = []

    for row in con.execute(
        "SELECT id, name, sort_order, is_active FROM category_groups ORDER BY id",
    ).fetchall():
        parts.append(f"g|{row[0]}|{row[1]}|{row[2]}|{int(bool(row[3]))}")

    for row in con.execute(
        "SELECT id, name, group_id, is_active,"
        " COALESCE(sheet_name, ''), COALESCE(sheet_group, '')"
        " FROM categories ORDER BY id",
    ).fetchall():
        parts.append(
            f"c|{row[0]}|{row[1]}|{row[2]}|{int(bool(row[3]))}|{row[4]}|{row[5]}",
        )

    for row in con.execute(
        "SELECT id, name, date_from, date_to, auto_attach_enabled, is_active"
        " FROM events ORDER BY id",
    ).fetchall():
        parts.append(
            f"e|{row[0]}|{row[1]}|{row[2]}|{row[3]}|{int(bool(row[4]))}|{int(bool(row[5]))}",
        )

    for row in con.execute(
        "SELECT id, name, is_active FROM tags ORDER BY id",
    ).fetchall():
        parts.append(f"t|{row[0]}|{row[1]}|{int(bool(row[2]))}")

    return "\n".join(parts).encode("utf-8")


def _hash_state(con: duckdb.DuckDBPyConnection) -> str:
    return hashlib.sha256(_canonical_state(con)).hexdigest()


def _commit_with_bump(
    con: duckdb.DuckDBPyConnection,
    before_hash: str,
    *,
    context: str,
) -> bool:
    """Finalise a mutation: bump ``catalog_version`` only if state changed, then COMMIT.

    Returns ``True`` when the version was bumped, ``False`` on no-op.
    """
    after_hash = _hash_state(con)
    bumped = False
    if before_hash != after_hash:
        previous = duckdb_repo.get_catalog_version(con)
        duckdb_repo.set_catalog_version(con, previous + 1)
        bumped = True
    con.execute("COMMIT")
    logger.info(
        "catalog_writer %s: %s",
        context,
        "bumped catalog_version" if bumped else "no-op (hash unchanged)",
    )
    return bumped


def _next_id(con: duckdb.DuckDBPyConnection, table: str) -> int:
    row = con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()  # noqa: S608
    return int(row[0]) if row else 1


# ---------------------------------------------------------------------------
# Usage-count helpers (soft-delete protection)
# ---------------------------------------------------------------------------


def _category_usage_count(con: duckdb.DuckDBPyConnection, category_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expenses WHERE category_id = ?",
        [category_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _event_usage_count(con: duckdb.DuckDBPyConnection, event_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expenses WHERE event_id = ?",
        [event_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _tag_usage_count(con: duckdb.DuckDBPyConnection, tag_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expense_tags WHERE tag_id = ?",
        [tag_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _group_usage_count(con: duckdb.DuckDBPyConnection, group_id: int) -> int:
    """Number of active categories pointing at this group."""
    row = con.execute(
        "SELECT COUNT(*) FROM categories WHERE group_id = ? AND is_active",
        [group_id],
    ).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Category groups
# ---------------------------------------------------------------------------


def add_group(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    sort_order: int | None = None,
) -> AddResult:
    """Create a new category group, or reactivate-in-place if the name exists.

    Reactivate preserves the existing ``sort_order`` (see module
    docstring for the reactivate contract). To change ``sort_order``,
    use ``edit_group``.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        existing = con.execute(
            "SELECT id, is_active FROM category_groups WHERE name = ?",
            [name],
        ).fetchone()
        if existing is not None:
            gid = int(existing[0])
            was_active = bool(existing[1])
            if not was_active:
                con.execute(
                    "UPDATE category_groups SET is_active = TRUE WHERE id = ?",
                    [gid],
                )
            bumped = _commit_with_bump(
                con,
                before,
                context=f"add_group(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=gid, status=status)
        gid = _next_id(con, "category_groups")
        if sort_order is None:
            row = con.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM category_groups",
            ).fetchone()
            sort_order = int(row[0]) if row else 1
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (?, ?, ?, TRUE)",
            [gid, name, sort_order],
        )
        _commit_with_bump(con, before, context=f"add_group(name={name!r})")
        return AddResult(id=gid, status="created")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.add_group")
        raise


def edit_group(
    con: duckdb.DuckDBPyConnection,
    group_id: int,
    *,
    name: str | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH for ``category_groups``.

    All parameters optional; absent parameters are left unchanged.
    ``is_active=False`` enforces the same in-use guard as the legacy
    ``set_group_active``. Validations (not-found, conflict, in-use)
    run *before* any UPDATE so a failed validation never leaves the
    row half-edited even if the caller PATCHes multiple columns at
    once.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id FROM category_groups WHERE id = ?",
            [group_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"category_group id={group_id} not found")
        # --- validate all inputs first ---
        if name is not None:
            conflict = con.execute(
                "SELECT id FROM category_groups WHERE name = ? AND id != ?",
                [name, group_id],
            ).fetchone()
            if conflict is not None:
                raise CatalogConflictError(
                    f"category_group name {name!r} already in use by id={int(conflict[0])}",
                )
        if is_active is False:
            usage = _group_usage_count(con, group_id)
            if usage > 0:
                raise CatalogInUseError("category_group", group_id, usage)
        # --- then apply ---
        if name is not None:
            con.execute(
                "UPDATE category_groups SET name = ? WHERE id = ?",
                [name, group_id],
            )
        if sort_order is not None:
            con.execute(
                "UPDATE category_groups SET sort_order = ? WHERE id = ?",
                [sort_order, group_id],
            )
        if is_active is not None:
            con.execute(
                "UPDATE category_groups SET is_active = ? WHERE id = ?",
                [bool(is_active), group_id],
            )
        _commit_with_bump(con, before, context=f"edit_group(id={group_id})")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.edit_group")
        raise


def set_group_active(
    con: duckdb.DuckDBPyConnection,
    group_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_group(is_active=...)``; kept for test readability."""
    edit_group(con, group_id, is_active=active)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def add_category(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    group_id: int,
    sheet_name: str | None = None,
    sheet_group: str | None = None,
) -> AddResult:
    """Create a new category, or reactivate-in-place if the name exists.

    Reactivate semantics:

    * Inactive match in the same group -> flip ``is_active=TRUE``,
      preserving ``sheet_name`` / ``sheet_group``; status
      ``"reactivated"``.
    * Inactive match in a different group -> flip ``is_active=TRUE``
      AND apply the caller's ``group_id`` (the add action's group
      selection is authoritative for a row that's currently out of
      service); status ``"reactivated"``.
    * Active match in the same group -> no-op, status ``"noop"``.
    * Active match in a **different** group -> ``CatalogConflictError``
      (409). "Add" is never the right verb for relocating an
      already-active category: the operator wanted either an explicit
      move (use ``edit_category(group_id=...)``), or they fat-fingered
      the group dropdown. Silently moving the row would retroactively
      change how historical expenses render in the PWA drill-down and
      is exactly the kind of hidden destruction the admin API is
      supposed to prevent.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        _require_active_group(con, group_id)
        existing = con.execute(
            "SELECT id, is_active, group_id FROM categories WHERE name = ?",
            [name],
        ).fetchone()
        if existing is not None:
            cid = int(existing[0])
            was_active = bool(existing[1])
            current_group = int(existing[2])
            if was_active and current_group != group_id:
                # Refuse the silent move. The operator must use PATCH
                # (``edit_category(group_id=...)``) if they really
                # intend to relocate an active category.
                raise CatalogConflictError(
                    f"category name {name!r} already active in a different group "
                    f"(current group_id={current_group}, requested {group_id}); "
                    "use edit_category to move it explicitly",
                )
            if not was_active:
                con.execute(
                    "UPDATE categories SET group_id = ?, is_active = TRUE WHERE id = ?",
                    [group_id, cid],
                )
            bumped = _commit_with_bump(
                con,
                before,
                context=f"add_category(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=cid, status=status)
        cid = _next_id(con, "categories")
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active, sheet_name, sheet_group)"
            " VALUES (?, ?, ?, TRUE, ?, ?)",
            [cid, name, group_id, sheet_name, sheet_group],
        )
        _commit_with_bump(con, before, context=f"add_category(name={name!r})")
        return AddResult(id=cid, status="created")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.add_category")
        raise


def edit_category(  # noqa: PLR0913, C901
    con: duckdb.DuckDBPyConnection,
    category_id: int,
    *,
    name: str | None = None,
    group_id: int | None = None,
    sheet_name: str | None = None,
    sheet_group: str | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH for ``categories``.

    All parameters optional. Validations (not-found, conflict,
    inactive-group, in-use) run *before* any UPDATE, so a failed
    validation never leaves the row half-edited even if the caller
    PATCHes multiple columns at once.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id FROM categories WHERE id = ?",
            [category_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"category id={category_id} not found")
        # --- validate all inputs first ---
        if name is not None:
            conflict = con.execute(
                "SELECT id FROM categories WHERE name = ? AND id != ?",
                [name, category_id],
            ).fetchone()
            if conflict is not None:
                raise CatalogConflictError(
                    f"category name {name!r} already in use by id={int(conflict[0])}",
                )
        if group_id is not None:
            _require_active_group(con, group_id)
        if is_active is False:
            usage = _category_usage_count(con, category_id)
            if usage > 0:
                raise CatalogInUseError("category", category_id, usage)
        # --- then apply ---
        if name is not None:
            con.execute(
                "UPDATE categories SET name = ? WHERE id = ?",
                [name, category_id],
            )
        if group_id is not None:
            con.execute(
                "UPDATE categories SET group_id = ? WHERE id = ?",
                [group_id, category_id],
            )
        # Empty string is the sentinel for "clear this column back
        # to NULL" — the PATCH body type is ``str | None`` where
        # ``None`` means "don't touch", so we need a second in-band
        # value for "explicitly reset". ``""`` is safe because an
        # empty ``sheet_name`` / ``sheet_group`` has no meaning
        # anywhere downstream (the drain worker reads them as
        # optional overrides), and the in-app editor will need this
        # affordance to remove stale mappings without dropping and
        # re-adding the category.
        if sheet_name is not None:
            con.execute(
                "UPDATE categories SET sheet_name = ? WHERE id = ?",
                [sheet_name if sheet_name != "" else None, category_id],
            )
        if sheet_group is not None:
            con.execute(
                "UPDATE categories SET sheet_group = ? WHERE id = ?",
                [sheet_group if sheet_group != "" else None, category_id],
            )
        if is_active is not None:
            con.execute(
                "UPDATE categories SET is_active = ? WHERE id = ?",
                [bool(is_active), category_id],
            )
        _commit_with_bump(con, before, context=f"edit_category(id={category_id})")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.edit_category")
        raise


def set_category_active(
    con: duckdb.DuckDBPyConnection,
    category_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_category(is_active=...)``; kept for test readability."""
    edit_category(con, category_id, is_active=active)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def add_event(
    con: duckdb.DuckDBPyConnection,
    *,
    name: str,
    date_from: date,
    date_to: date,
    auto_attach_enabled: bool = False,
) -> AddResult:
    """Create a new event, or reactivate-in-place if the name exists.

    Reactivate behaviour: ``date_from`` / ``date_to`` /
    ``auto_attach_enabled`` on the existing row are left untouched.
    To change those, use ``edit_event``.

    Date-range validation (``date_from <= date_to``) runs only on the
    INSERT branch. On reactivate we discard the caller's dates anyway,
    so rejecting an already-named event because the operator happened
    to type today's date in both widgets in the wrong order would
    surface a confusing 422 for a path that never applies them. The
    row's stored range stayed well-formed at write time and is not
    being mutated here.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        existing = con.execute(
            "SELECT id, is_active FROM events WHERE name = ?",
            [name],
        ).fetchone()
        if existing is not None:
            eid = int(existing[0])
            was_active = bool(existing[1])
            if not was_active:
                con.execute(
                    "UPDATE events SET is_active = TRUE WHERE id = ?",
                    [eid],
                )
            bumped = _commit_with_bump(
                con,
                before,
                context=f"add_event(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=eid, status=status)
        if date_from > date_to:
            # Validated only here (fresh-insert branch) because the
            # reactivate path above never reads these values.
            raise CatalogWriteError(
                f"event date_from ({date_from}) must be <= date_to ({date_to})",
                http_status=422,
            )
        eid = _next_id(con, "events")
        con.execute(
            "INSERT INTO events (id, name, date_from, date_to, auto_attach_enabled, is_active)"
            " VALUES (?, ?, ?, ?, ?, TRUE)",
            [eid, name, date_from, date_to, auto_attach_enabled],
        )
        _commit_with_bump(con, before, context=f"add_event(name={name!r})")
        return AddResult(id=eid, status="created")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.add_event")
        raise


def edit_event(  # noqa: PLR0913, C901
    con: duckdb.DuckDBPyConnection,
    event_id: int,
    *,
    name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    auto_attach_enabled: bool | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH for ``events``.

    All parameters optional. Validations (not-found, conflict,
    post-patch date range, in-use) run *before* any UPDATE so a
    failed validation never leaves the row half-edited. The date
    range check is evaluated against the composite "current row
    merged with patch values" so patching only one of ``date_from``
    / ``date_to`` is still validated correctly.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id, date_from, date_to FROM events WHERE id = ?",
            [event_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"event id={event_id} not found")
        new_from = date_from if date_from is not None else row[1]
        new_to = date_to if date_to is not None else row[2]
        # --- validate all inputs first ---
        if new_from > new_to:
            raise CatalogWriteError(
                f"event date_from ({new_from}) must be <= date_to ({new_to})",
                http_status=422,
            )
        if name is not None:
            conflict = con.execute(
                "SELECT id FROM events WHERE name = ? AND id != ?",
                [name, event_id],
            ).fetchone()
            if conflict is not None:
                raise CatalogConflictError(
                    f"event name {name!r} already in use by id={int(conflict[0])}",
                )
        if is_active is False:
            usage = _event_usage_count(con, event_id)
            if usage > 0:
                raise CatalogInUseError("event", event_id, usage)
        # --- then apply ---
        if name is not None:
            con.execute("UPDATE events SET name = ? WHERE id = ?", [name, event_id])
        if date_from is not None:
            con.execute(
                "UPDATE events SET date_from = ? WHERE id = ?",
                [date_from, event_id],
            )
        if date_to is not None:
            con.execute(
                "UPDATE events SET date_to = ? WHERE id = ?",
                [date_to, event_id],
            )
        if auto_attach_enabled is not None:
            con.execute(
                "UPDATE events SET auto_attach_enabled = ? WHERE id = ?",
                [bool(auto_attach_enabled), event_id],
            )
        if is_active is not None:
            con.execute(
                "UPDATE events SET is_active = ? WHERE id = ?",
                [bool(is_active), event_id],
            )
        _commit_with_bump(con, before, context=f"edit_event(id={event_id})")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.edit_event")
        raise


def set_event_active(
    con: duckdb.DuckDBPyConnection,
    event_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_event(is_active=...)``; kept for test readability."""
    edit_event(con, event_id, is_active=active)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def add_tag(con: duckdb.DuckDBPyConnection, *, name: str) -> AddResult:
    """Create a new tag, or reactivate-in-place if the name exists."""
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        existing = con.execute(
            "SELECT id, is_active FROM tags WHERE name = ?",
            [name],
        ).fetchone()
        if existing is not None:
            tid = int(existing[0])
            was_active = bool(existing[1])
            if not was_active:
                con.execute("UPDATE tags SET is_active = TRUE WHERE id = ?", [tid])
            bumped = _commit_with_bump(
                con,
                before,
                context=f"add_tag(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=tid, status=status)
        tid = _next_id(con, "tags")
        con.execute(
            "INSERT INTO tags (id, name, is_active) VALUES (?, ?, TRUE)",
            [tid, name],
        )
        _commit_with_bump(con, before, context=f"add_tag(name={name!r})")
        return AddResult(id=tid, status="created")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.add_tag")
        raise


def edit_tag(
    con: duckdb.DuckDBPyConnection,
    tag_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH for ``tags``.

    All parameters optional. Validations (not-found, conflict,
    in-use) run *before* any UPDATE so a failed validation never
    leaves the row half-edited.
    """
    con.execute("BEGIN")
    try:
        before = _hash_state(con)
        row = con.execute("SELECT id FROM tags WHERE id = ?", [tag_id]).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"tag id={tag_id} not found")
        # --- validate all inputs first ---
        if name is not None:
            conflict = con.execute(
                "SELECT id FROM tags WHERE name = ? AND id != ?",
                [name, tag_id],
            ).fetchone()
            if conflict is not None:
                raise CatalogConflictError(
                    f"tag name {name!r} already in use by id={int(conflict[0])}",
                )
        if is_active is False:
            usage = _tag_usage_count(con, tag_id)
            if usage > 0:
                raise CatalogInUseError("tag", tag_id, usage)
        # --- then apply ---
        if name is not None:
            con.execute("UPDATE tags SET name = ? WHERE id = ?", [name, tag_id])
        if is_active is not None:
            con.execute(
                "UPDATE tags SET is_active = ? WHERE id = ?",
                [bool(is_active), tag_id],
            )
        _commit_with_bump(con, before, context=f"edit_tag(id={tag_id})")
    except Exception:
        duckdb_repo.best_effort_rollback(con, context="catalog_writer.edit_tag")
        raise


def set_tag_active(
    con: duckdb.DuckDBPyConnection,
    tag_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_tag(is_active=...)``; kept for test readability."""
    edit_tag(con, tag_id, is_active=active)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_active_group(con: duckdb.DuckDBPyConnection, group_id: int) -> None:
    row = con.execute(
        "SELECT is_active FROM category_groups WHERE id = ?",
        [group_id],
    ).fetchone()
    if row is None:
        raise CatalogWriteError(
            f"category_group id={group_id} not found",
            http_status=422,
        )
    if not bool(row[0]):
        raise CatalogWriteError(
            f"category_group id={group_id} is inactive; reactivate it first",
            http_status=422,
        )
