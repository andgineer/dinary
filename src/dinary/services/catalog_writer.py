"""Admin-API write path for the catalog tables.

Every mutation that flows through ``dinary.api.admin_catalog`` lands
here. Each public method:

1. Opens one SQLite write transaction (``BEGIN IMMEDIATE``).
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

* ``delete_category`` / ``delete_event`` / ``delete_tag`` auto-degrade
  to soft-delete (``is_active=FALSE``) when the row is still
  referenced by any ``expenses`` row or mapping table; no 409 is
  raised — the caller inspects ``DeleteResult.status`` instead.
* ``edit_category`` / ``edit_event`` / ``edit_tag`` treat a referenced
  row like any other: any subset of columns, including
  ``is_active=FALSE`` combined with a rename or ``group_id`` move, is
  allowed. SQLite's FK engine enforces referential integrity on
  ``DELETE`` and on ``UPDATE`` of referenced key columns; ``UPDATE``
  of non-key columns on a row that still has incoming references is
  accepted unchanged, so no extra guard is needed here to stay
  FK-safe.
* ``delete_group`` refuses when the group still has any child
  categories (active or inactive) — surfaces as
  ``CatalogInUseError`` with the child-category count so admin API
  can translate to 409. Same for ``edit_group`` with
  ``is_active=FALSE`` while child categories still point at it.
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

``imports.seed.rebuild_config_from_sheets`` (the ``inv import-catalog``
entry point) does **not** flow through this module. It wraps its
whole catalog rebuild in a single long transaction and calls
``seed_config._bump_catalog_version`` once at the end, which
satisfies the "catalog change bumps version" invariant via a
different path. The two write paths are kept separate because
``catalog_writer`` opens per-mutation transactions that cannot nest
inside the seed's outer ``BEGIN/COMMIT``. A future unification would
require restructuring the seed to commit per entity; that refactor is
out of scope here. Until then, both paths funnel version writes
through ``ledger_repo.set_catalog_version`` so any future audit hook
can intercept them uniformly.
"""

import hashlib
import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Literal

from dinary.services import ledger_repo
from dinary.services.sheet_mapping import decode_auto_tags_value

logger = logging.getLogger(__name__)


CatalogKind = Literal["category_group", "category", "event", "tag"]

AddStatus = Literal["created", "reactivated", "noop"]

#: ``hard`` = row physically removed; ``soft`` = row flipped to
#: ``is_active=FALSE`` because it's still referenced by the ledger and
#: removing it would orphan historical rows. The admin API surfaces
#: the distinction so the PWA can tell the operator "still available
#: under Show inactive" vs "gone for good".
DeleteStatus = Literal["hard", "soft"]


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Return value of ``delete_*`` helpers.

    ``status`` reports whether the row was physically removed or
    soft-retired (``is_active=FALSE``). ``usage_count`` is the number
    of referencing ledger rows observed at decision time — zero for
    the hard-delete branch, >0 for the soft-delete branch.
    """

    status: DeleteStatus
    usage_count: int


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
    """Delete or deactivate blocked because the row is still referenced.

    Only raised for ``category_group``: ``usage_count`` counts child
    categories (active or inactive). A group with any children cannot
    be deleted or deactivated until the categories are relocated or
    removed.

    Categories / events / tags do *not* raise this — ``delete_*`` on a
    referenced row auto-degrades to soft-delete (see
    ``DeleteResult.status``) and ``edit_*`` on a referenced row
    accepts any column mix including ``is_active=FALSE`` combined with
    rename / ``group_id`` move. The former is a policy choice ("retire
    but keep pointable"); the latter is safe because SQLite enforces
    FK constraints on ``DELETE`` (and on ``UPDATE`` of referenced key
    columns) only, not on ``UPDATE`` of non-key columns.
    """

    http_status = 409

    def __init__(self, kind: CatalogKind, row_id: int, usage_count: int) -> None:
        if kind == "category_group":
            detail = f"still has {usage_count} child categor{'y' if usage_count == 1 else 'ies'}"
            hint = "relocate or delete the categories first"
        else:
            detail = f"still referenced by {usage_count} expense row(s)"
            hint = "retire the referencing expenses first"
        super().__init__(f"{kind} id={row_id} is {detail}; {hint}")
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


def hash_catalog_state(con: sqlite3.Connection) -> str:
    """Return a hex sha256 over the canonical catalog state.

    Public re-export of ``_hash_state`` so write paths outside this
    module (notably ``imports.seed.rebuild_config_from_sheets``) can
    gate their ``catalog_version`` bump on the same invariant this
    module enforces: version only changes when the observable catalog
    state does. Using the same helper in both paths guarantees a
    single definition of "observable".
    """
    return _hash_state(con)


def _canonical_state(con: sqlite3.Connection) -> bytes:
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
        "SELECT id, name, date_from, date_to, auto_attach_enabled, is_active,"
        " COALESCE(auto_tags, '[]')"
        " FROM events ORDER BY id",
    ).fetchall():
        parts.append(
            f"e|{row[0]}|{row[1]}|{row[2]}|{row[3]}|{int(bool(row[4]))}|"
            f"{int(bool(row[5]))}|{row[6]}",
        )

    for row in con.execute(
        "SELECT id, name, is_active FROM tags ORDER BY id",
    ).fetchall():
        parts.append(f"t|{row[0]}|{row[1]}|{int(bool(row[2]))}")

    return "\n".join(parts).encode("utf-8")


def _hash_state(con: sqlite3.Connection) -> str:
    return hashlib.sha256(_canonical_state(con)).hexdigest()


def _commit_with_bump(
    con: sqlite3.Connection,
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
        previous = ledger_repo.get_catalog_version(con)
        ledger_repo.set_catalog_version(con, previous + 1)
        bumped = True
    con.execute("COMMIT")
    logger.info(
        "catalog_writer %s: %s",
        context,
        "bumped catalog_version" if bumped else "no-op (hash unchanged)",
    )
    return bumped


def _next_id(con: sqlite3.Connection, table: str) -> int:
    row = con.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()  # noqa: S608
    return int(row[0]) if row else 1


# ---------------------------------------------------------------------------
# Usage-count helpers (soft-delete protection)
# ---------------------------------------------------------------------------


def _category_usage_count(con: sqlite3.Connection, category_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expenses WHERE category_id = ?",
        [category_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _event_usage_count(con: sqlite3.Connection, event_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expenses WHERE event_id = ?",
        [event_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _tag_usage_count(con: sqlite3.Connection, tag_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expense_tags WHERE tag_id = ?",
        [tag_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _group_usage_count(con: sqlite3.Connection, group_id: int) -> int:
    """Active child count for observability in ``edit_group``.

    Distinct from ``_group_child_category_count`` (used by
    ``delete_group``) which counts *all* children, active or not. The
    distinction is intentional:

    * ``edit_group(is_active=False)`` asks "is this group still
      relevant to the operator?" — an inactive category is already
      hidden from new expenses, so it should not block flipping the
      parent inactive.
    * ``delete_group`` asks "is it safe to physically remove this row?"
      — FK from ``categories.group_id`` does not care about
      ``is_active`` so any surviving category row, active or not, will
      cause a constraint violation.
    """
    row = con.execute(
        "SELECT COUNT(*) FROM categories WHERE group_id = ? AND is_active",
        [group_id],
    ).fetchone()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Mapping-reference helpers (hard-delete FK protection)
#
# ``delete_*`` must force a soft delete whenever the row is referenced
# by any FK-bearing table, not just by ``expenses`` / ``expense_tags``.
# The ``sheet_mapping`` + ``import_mapping`` families also carry FKs
# into ``categories`` / ``events`` / ``tags``; if a mapping row points
# at the target, ``DELETE`` would violate the constraint at COMMIT.
#
# The counts returned by these helpers are *not* surfaced in the
# ``usage_count`` field of ``DeleteResult`` — the API contract there
# says "how many expenses reference this", which is what the UI needs
# to phrase "used by N expenses". Mapping references are an
# implementation detail of the delete safety check.
# ---------------------------------------------------------------------------


def _category_mapping_reference_count(
    con: sqlite3.Connection,
    category_id: int,
) -> int:
    row = con.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM sheet_mapping WHERE category_id = ?) "
        " + (SELECT COUNT(*) FROM import_mapping WHERE category_id = ?)",
        [category_id, category_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _event_mapping_reference_count(
    con: sqlite3.Connection,
    event_id: int,
) -> int:
    row = con.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM sheet_mapping WHERE event_id = ?) "
        " + (SELECT COUNT(*) FROM import_mapping WHERE event_id = ?)",
        [event_id, event_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _tag_mapping_reference_count(
    con: sqlite3.Connection,
    tag_id: int,
) -> int:
    """Mapping-table reference count for a tag.

    Includes ``sheet_mapping_tags`` + ``import_mapping_tags`` (both FK
    into ``tags``) **and** ``events.auto_tags`` — which is denormalised
    JSON of tag *names*, not ids, so SQLite's FK engine won't catch it.
    Counting the name reference here means hard-delete refuses while
    any event still lists the tag, keeping the runtime auto-attach
    contract ("event carrying this tag in ``auto_tags`` unions it
    into every attached expense") well-defined.
    """
    tag_name_row = con.execute(
        "SELECT name FROM tags WHERE id = ?",
        [tag_id],
    ).fetchone()
    tag_name = str(tag_name_row[0]) if tag_name_row else None
    auto_tag_refs = 0
    if tag_name is not None:
        auto_tag_refs = _events_auto_tags_reference_count(con, tag_name)
    row = con.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM sheet_mapping_tags WHERE tag_id = ?) "
        " + (SELECT COUNT(*) FROM import_mapping_tags WHERE tag_id = ?)",
        [tag_id, tag_id],
    ).fetchone()
    mapping_refs = int(row[0]) if row else 0
    return mapping_refs + auto_tag_refs


def _events_auto_tags_reference_count(
    con: sqlite3.Connection,
    tag_name: str,
) -> int:
    """Count events whose ``auto_tags`` JSON array contains ``tag_name``.

    SQLite does not enforce JSON-array semantics, so we load + decode
    every non-empty ``auto_tags`` payload and check membership in
    Python. The table is small (one row per historical year plus a
    handful of explicit events) so scanning is cheap. Decoding goes
    through ``decode_auto_tags_value`` so the null/malformed handling
    stays in lockstep with every other reader of ``events.auto_tags``.
    """
    rows = con.execute(
        "SELECT id, auto_tags FROM events"
        " WHERE auto_tags IS NOT NULL AND auto_tags != '' AND auto_tags != '[]'",
    ).fetchall()
    count = 0
    for event_id, raw in rows:
        decoded = decode_auto_tags_value(raw, context=f"event_id={int(event_id)}")
        if tag_name in decoded:
            count += 1
    return count


# ---------------------------------------------------------------------------
# Category groups
# ---------------------------------------------------------------------------


def add_group(
    con: sqlite3.Connection,
    *,
    name: str,
    sort_order: int | None = None,
) -> AddResult:
    """Create a new category group, or reactivate-in-place if the name exists.

    Reactivate preserves the existing ``sort_order`` (see module
    docstring for the reactivate contract). To change ``sort_order``,
    use ``edit_group``.
    """
    con.execute("BEGIN IMMEDIATE")
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
        ledger_repo.best_effort_rollback(con, context="catalog_writer.add_group")
        raise


def edit_group(
    con: sqlite3.Connection,
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
    con.execute("BEGIN IMMEDIATE")
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
        ledger_repo.best_effort_rollback(con, context="catalog_writer.edit_group")
        raise


def set_group_active(
    con: sqlite3.Connection,
    group_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_group(is_active=...)``; kept for test readability."""
    edit_group(con, group_id, is_active=active)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


def add_category(
    con: sqlite3.Connection,
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
    con.execute("BEGIN IMMEDIATE")
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
        ledger_repo.best_effort_rollback(con, context="catalog_writer.add_category")
        raise


def edit_category(  # noqa: PLR0912, PLR0913, C901
    con: sqlite3.Connection,
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
    inactive-group) run *before* any UPDATE, so a failed validation
    never leaves the row half-edited even if the caller PATCHes
    multiple columns at once.

    ``is_active=False`` always succeeds on a known row (soft-retire).
    The in-use guard was intentionally dropped: ``DELETE`` on the same
    row already flips ``is_active=False`` when the ledger still
    references it, and having PATCH refuse the same end-state with 409
    was a confusing asymmetry. Operators use PATCH to flip the flag
    either direction; DELETE is for "actually try to remove the row".
    """
    con.execute("BEGIN IMMEDIATE")
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
        ledger_repo.best_effort_rollback(con, context="catalog_writer.edit_category")
        raise


def set_category_active(
    con: sqlite3.Connection,
    category_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_category(is_active=...)``; kept for test readability."""
    edit_category(con, category_id, is_active=active)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _encode_auto_tags(auto_tags: list[str] | tuple[str, ...] | None) -> str:
    """Encode an ``auto_tags`` list for storage on ``events.auto_tags``.

    ``None`` means "caller did not supply a value" and is the empty
    array on INSERT. Caller-supplied empty list is also stored as
    ``'[]'`` (explicit "no auto-tags").
    """
    names = list(auto_tags) if auto_tags is not None else []
    return json.dumps(names, ensure_ascii=False)


def add_event(  # noqa: PLR0913
    con: sqlite3.Connection,
    *,
    name: str,
    date_from: date,
    date_to: date,
    auto_attach_enabled: bool = False,
    auto_tags: list[str] | tuple[str, ...] | None = None,
) -> AddResult:
    """Create a new event, or reactivate-in-place if the name exists.

    Reactivate behaviour: ``date_from`` / ``date_to`` /
    ``auto_attach_enabled`` / ``auto_tags`` on the existing row are
    left untouched. To change those, use ``edit_event``.

    Input validation (``date_from <= date_to``, ``auto_tags`` names
    resolve to an existing ``tags`` row — active or inactive) runs
    regardless of whether we insert or reactivate. The reactivate
    path discards the caller's values, but the API contract is
    "supply a valid body" on every call — an invalid body surfaces
    here as 422 instead of being silently ignored. This keeps
    ``add_event`` symmetric with ``edit_event`` (which rejects the
    same inputs) and prevents the operator from mistaking "the
    caller sent garbage which got dropped" for "reactivated with
    the new values".
    """
    if date_from > date_to:
        raise CatalogWriteError(
            f"event date_from ({date_from}) must be <= date_to ({date_to})",
            http_status=422,
        )
    con.execute("BEGIN IMMEDIATE")
    try:
        before = _hash_state(con)
        _require_known_tag_names(con, auto_tags or ())
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
        eid = _next_id(con, "events")
        con.execute(
            "INSERT INTO events"
            " (id, name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
            " VALUES (?, ?, ?, ?, ?, TRUE, ?)",
            [
                eid,
                name,
                date_from,
                date_to,
                auto_attach_enabled,
                _encode_auto_tags(auto_tags),
            ],
        )
        _commit_with_bump(con, before, context=f"add_event(name={name!r})")
        return AddResult(id=eid, status="created")
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.add_event")
        raise


def edit_event(  # noqa: PLR0912, PLR0913, C901
    con: sqlite3.Connection,
    event_id: int,
    *,
    name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    auto_attach_enabled: bool | None = None,
    auto_tags: list[str] | tuple[str, ...] | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH for ``events``.

    All parameters optional. ``auto_tags=None`` means "leave column
    alone"; an empty list explicitly clears it. Validations
    (not-found, conflict, post-patch date range, in-use, unknown
    auto_tag names) run *before* any UPDATE so a failed validation
    never leaves the row half-edited. The date range check is
    evaluated against the composite "current row merged with patch
    values" so patching only one of ``date_from`` / ``date_to`` is
    still validated correctly.
    """
    con.execute("BEGIN IMMEDIATE")
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
        # ``is_active=False`` is always allowed (soft-retire). See the
        # ``edit_category`` docstring for the rationale — PATCH and
        # DELETE must not disagree on the same end-state.
        if auto_tags is not None:
            _require_known_tag_names(con, auto_tags)
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
        if auto_tags is not None:
            con.execute(
                "UPDATE events SET auto_tags = ? WHERE id = ?",
                [_encode_auto_tags(auto_tags), event_id],
            )
        if is_active is not None:
            con.execute(
                "UPDATE events SET is_active = ? WHERE id = ?",
                [bool(is_active), event_id],
            )
        _commit_with_bump(con, before, context=f"edit_event(id={event_id})")
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.edit_event")
        raise


def set_event_active(
    con: sqlite3.Connection,
    event_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_event(is_active=...)``; kept for test readability."""
    edit_event(con, event_id, is_active=active)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


def add_tag(con: sqlite3.Connection, *, name: str) -> AddResult:
    """Create a new tag, or reactivate-in-place if the name exists."""
    # Name validation is a pure string check — run it before we open a
    # transaction so an invalid name does not cost a BEGIN/ROLLBACK
    # round-trip. Kept consistent with ``edit_tag``.
    _validate_tag_name(name)
    con.execute("BEGIN IMMEDIATE")
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
        ledger_repo.best_effort_rollback(con, context="catalog_writer.add_tag")
        raise


def edit_tag(
    con: sqlite3.Connection,
    tag_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH for ``tags``.

    All parameters optional. Validations (not-found, conflict) run
    *before* any UPDATE so a failed validation never leaves the row
    half-edited. ``is_active=False`` always succeeds on a known row
    (soft-retire); see ``edit_category`` docstring for the rationale.

    Rename cascade into ``events.auto_tags``: the auto-tag column is
    a denormalised JSON array of tag *names*, not ids. A rename that
    leaves ``events.auto_tags`` untouched would silently break the
    auto-attach contract (the event would reference a name that no
    longer exists in the ``tags`` table). We rewrite every event row
    whose ``auto_tags`` mentions the old name so the invariant
    "every name in ``auto_tags`` resolves to a known tag row
    (active or inactive)" is preserved atomically inside this same
    transaction.
    """
    # Run the pure-string name check before BEGIN so a malformed name
    # does not cost a transaction (mirrors ``add_tag``).
    if name is not None:
        _validate_tag_name(name)
    con.execute("BEGIN IMMEDIATE")
    try:
        before = _hash_state(con)
        row = con.execute("SELECT id, name FROM tags WHERE id = ?", [tag_id]).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"tag id={tag_id} not found")
        old_name = str(row[1])
        # --- DB-state validations ---
        if name is not None:
            conflict = con.execute(
                "SELECT id FROM tags WHERE name = ? AND id != ?",
                [name, tag_id],
            ).fetchone()
            if conflict is not None:
                raise CatalogConflictError(
                    f"tag name {name!r} already in use by id={int(conflict[0])}",
                )
        # --- then apply ---
        if name is not None:
            con.execute("UPDATE tags SET name = ? WHERE id = ?", [name, tag_id])
            if name != old_name:
                _rename_tag_in_events_auto_tags(con, old_name=old_name, new_name=name)
        if is_active is not None:
            con.execute(
                "UPDATE tags SET is_active = ? WHERE id = ?",
                [bool(is_active), tag_id],
            )
        _commit_with_bump(con, before, context=f"edit_tag(id={tag_id})")
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.edit_tag")
        raise


def _rename_tag_in_events_auto_tags(
    con: sqlite3.Connection,
    *,
    old_name: str,
    new_name: str,
) -> None:
    """Rewrite every ``events.auto_tags`` payload that references ``old_name``.

    Called from ``edit_tag`` when the ``tags.name`` column changes.
    Preserves array order (so authoring order semantics from
    ``resolve_event_auto_tag_ids`` stay intact) and idempotently
    dedups ``new_name`` if it already happened to be in the same
    array. Empty / malformed payloads are left alone — the generic
    reader (``decode_auto_tags_value``) already treats them as empty.
    """
    rows = con.execute(
        "SELECT id, auto_tags FROM events"
        " WHERE auto_tags IS NOT NULL AND auto_tags != '' AND auto_tags != '[]'",
    ).fetchall()
    for event_id, raw in rows:
        decoded = decode_auto_tags_value(raw, context=f"event_id={int(event_id)}")
        if old_name not in decoded:
            continue
        renamed: list[str] = []
        for value in decoded:
            candidate = new_name if value == old_name else value
            if candidate not in renamed:
                renamed.append(candidate)
        con.execute(
            "UPDATE events SET auto_tags = ? WHERE id = ?",
            [json.dumps(renamed, ensure_ascii=False), int(event_id)],
        )


def set_tag_active(
    con: sqlite3.Connection,
    tag_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_tag(is_active=...)``; kept for test readability."""
    edit_tag(con, tag_id, is_active=active)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_active_group(con: sqlite3.Connection, group_id: int) -> None:
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


#: Characters rejected inside tag names. Tag names flow through the
#: ``map`` tab's comma/whitespace-separated tags cell and through
#: ``events.auto_tags`` (a JSON array of bare names), so whitespace
#: would split a single tag into two lookups and a comma would do
#: the same; both look like honest typos but silently route expenses
#: into the wrong envelope. Reject them at write time.
_DISALLOWED_TAG_NAME_RE = re.compile(r"[,\s]")


def _validate_tag_name(name: str) -> None:
    if _DISALLOWED_TAG_NAME_RE.search(name):
        raise CatalogWriteError(
            f"tag name {name!r} contains whitespace or ','; the map tab uses "
            "these as separators so they cannot appear inside a single name",
            http_status=422,
        )


def _require_known_tag_names(
    con: sqlite3.Connection,
    names: list[str] | tuple[str, ...],
) -> None:
    """422 if any name is not present in the ``tags`` table at all.

    ``events.auto_tags`` is a denormalised name array (keeping a
    ``tag_id`` array would require a second catalog table just for
    events). We validate at write time that every name resolves to
    a ``tags`` row so typos don't silently route to the "unknown tag"
    drop path in ``resolve_event_auto_tag_ids``. The ``is_active``
    flag is deliberately not checked — it means "hide from the
    ручной пикер", and events must keep auto-attaching tags that the
    operator has retired from the picker (e.g. the "отпуск" tag is
    only set automatically when a vacation event is picked, so it is
    hidden from the manual picker while still being a valid auto-tag
    name).
    """
    unique = sorted({str(n) for n in names})
    if not unique:
        return
    placeholders = ",".join(["?"] * len(unique))
    rows = con.execute(
        f"SELECT name FROM tags WHERE name IN ({placeholders})",  # noqa: S608
        unique,
    ).fetchall()
    found = {str(r[0]) for r in rows}
    missing = [n for n in unique if n not in found]
    if missing:
        raise CatalogWriteError(
            f"auto_tags references unknown tag name(s) {missing}; create the tag first",
            http_status=422,
        )


# ---------------------------------------------------------------------------
# Delete endpoints (soft/hard semantics)
# ---------------------------------------------------------------------------
#
# Hard-delete iff the row has no ledger references; otherwise flip
# ``is_active=FALSE`` and report ``soft`` so the caller can surface
# "still available under Show inactive" in the UI.
#
# ``category_groups`` use a different definition of "referenced":
# ledger rows FK into categories, not groups, so a group is removable
# iff no category (active OR inactive) points at it. That keeps
# "retire this unused group" safe while still refusing "hard-remove a
# group whose inactive categories might be reactivated later".


def _group_child_category_count(
    con: sqlite3.Connection,
    group_id: int,
) -> int:
    """Total categories (active or inactive) pointing at this group.

    Used by ``delete_group`` to gate physical removal: a non-zero
    count means the ``categories.group_id`` FK would reject the
    ``DELETE``, so the operator has to relocate or delete the
    children first.
    """
    row = con.execute(
        "SELECT COUNT(*) FROM categories WHERE group_id = ?",
        [group_id],
    ).fetchone()
    return int(row[0]) if row else 0


def delete_group(
    con: sqlite3.Connection,
    group_id: int,
) -> DeleteResult:
    """Hard-delete a category group iff it has no (active or inactive)
    categories referencing it; raise 409 otherwise.

    Unlike the other catalog kinds, soft-deleting a group while
    categories still point at it leaves orphaned ``is_active=TRUE``
    categories attached to an inactive group — the API refuses that
    combination outright and forces the operator to relocate (or
    delete) the categories first.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id FROM category_groups WHERE id = ?",
            [group_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"category_group id={group_id} not found")
        child_count = _group_child_category_count(con, group_id)
        if child_count > 0:
            raise CatalogInUseError("category_group", group_id, child_count)
        con.execute("DELETE FROM category_groups WHERE id = ?", [group_id])
        _commit_with_bump(con, before, context=f"delete_group(id={group_id})")
        return DeleteResult(status="hard", usage_count=0)
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.delete_group")
        raise


def delete_category(
    con: sqlite3.Connection,
    category_id: int,
) -> DeleteResult:
    """Hard-delete iff nothing references this category; otherwise flip
    ``is_active=FALSE``.

    "Nothing references" means no ``expenses`` row *and* no
    ``sheet_mapping`` / ``import_mapping`` row points at it. Mapping
    references would otherwise trip the FK constraint at COMMIT time
    even when the ledger is clean.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id, is_active FROM categories WHERE id = ?",
            [category_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"category id={category_id} not found")
        usage = _category_usage_count(con, category_id)
        mapping_refs = _category_mapping_reference_count(con, category_id)
        if usage == 0 and mapping_refs == 0:
            con.execute("DELETE FROM categories WHERE id = ?", [category_id])
            _commit_with_bump(con, before, context=f"delete_category(hard id={category_id})")
            return DeleteResult(status="hard", usage_count=0)
        con.execute(
            "UPDATE categories SET is_active = FALSE WHERE id = ?",
            [category_id],
        )
        _commit_with_bump(con, before, context=f"delete_category(soft id={category_id})")
        return DeleteResult(status="soft", usage_count=usage)
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.delete_category")
        raise


def delete_event(
    con: sqlite3.Connection,
    event_id: int,
) -> DeleteResult:
    """Hard-delete iff nothing references this event; otherwise flip
    ``is_active=FALSE``.

    See ``delete_category`` for the expanded "nothing references"
    definition — mapping rows must also be absent.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id FROM events WHERE id = ?",
            [event_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"event id={event_id} not found")
        usage = _event_usage_count(con, event_id)
        mapping_refs = _event_mapping_reference_count(con, event_id)
        if usage == 0 and mapping_refs == 0:
            con.execute("DELETE FROM events WHERE id = ?", [event_id])
            _commit_with_bump(con, before, context=f"delete_event(hard id={event_id})")
            return DeleteResult(status="hard", usage_count=0)
        con.execute(
            "UPDATE events SET is_active = FALSE WHERE id = ?",
            [event_id],
        )
        _commit_with_bump(con, before, context=f"delete_event(soft id={event_id})")
        return DeleteResult(status="soft", usage_count=usage)
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.delete_event")
        raise


def delete_tag(
    con: sqlite3.Connection,
    tag_id: int,
) -> DeleteResult:
    """Hard-delete iff nothing references this tag; otherwise flip
    ``is_active=FALSE``.

    "Nothing references" covers ``expense_tags``, ``sheet_mapping_tags``
    and ``import_mapping_tags`` — any surviving row would trip the FK
    constraint at COMMIT.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        before = _hash_state(con)
        row = con.execute(
            "SELECT id FROM tags WHERE id = ?",
            [tag_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"tag id={tag_id} not found")
        usage = _tag_usage_count(con, tag_id)
        mapping_refs = _tag_mapping_reference_count(con, tag_id)
        if usage == 0 and mapping_refs == 0:
            con.execute("DELETE FROM tags WHERE id = ?", [tag_id])
            _commit_with_bump(con, before, context=f"delete_tag(hard id={tag_id})")
            return DeleteResult(status="hard", usage_count=0)
        con.execute(
            "UPDATE tags SET is_active = FALSE WHERE id = ?",
            [tag_id],
        )
        _commit_with_bump(con, before, context=f"delete_tag(soft id={tag_id})")
        return DeleteResult(status="soft", usage_count=usage)
    except Exception:
        ledger_repo.best_effort_rollback(con, context="catalog_writer.delete_tag")
        raise
