"""Categories CRUD.

Shared helpers, exception classes, and state-hashing utilities live in
``services.catalog_writer`` and are imported from there.
"""

import sqlite3

from dinary.api.controllers.catalog_writer import commit_with_bump, hash_state, next_id
from dinary.api.controllers.catalog_writer_errors import (
    AddResult,
    AddStatus,
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogWriteError,
    DeleteResult,
)
from dinary.db import storage


def _category_usage_count(con: sqlite3.Connection, category_id: int) -> int:
    row = con.execute(
        "SELECT COUNT(*) FROM expenses WHERE category_id = ?",
        [category_id],
    ).fetchone()
    return int(row[0]) if row else 0


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


def _validate_category_edit(
    con: sqlite3.Connection,
    category_id: int,
    name: str | None,
    group_id: int | None,
) -> None:
    row = con.execute("SELECT id FROM categories WHERE id = ?", [category_id]).fetchone()
    if row is None:
        raise CatalogNotFoundError(f"category id={category_id} not found")
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
        before = hash_state(con)
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
            bumped = commit_with_bump(
                con,
                before,
                context=f"add_category(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=cid, status=status)
        cid = next_id(con, "categories")
        con.execute(
            "INSERT INTO categories (id, name, group_id, is_active, sheet_name, sheet_group)"
            " VALUES (?, ?, ?, TRUE, ?, ?)",
            [cid, name, group_id, sheet_name, sheet_group],
        )
        commit_with_bump(con, before, context=f"add_category(name={name!r})")
        return AddResult(id=cid, status="created")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.add_category")
        raise


def edit_category(
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
        before = hash_state(con)
        _validate_category_edit(con, category_id, name, group_id)
        if name is not None:
            con.execute("UPDATE categories SET name = ? WHERE id = ?", [name, category_id])
        if group_id is not None:
            con.execute("UPDATE categories SET group_id = ? WHERE id = ?", [group_id, category_id])
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
        commit_with_bump(con, before, context=f"edit_category(id={category_id})")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.edit_category")
        raise


def set_category_active(
    con: sqlite3.Connection,
    category_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_category(is_active=...)``; kept for test readability."""
    edit_category(con, category_id, is_active=active)


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
        before = hash_state(con)
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
            commit_with_bump(con, before, context=f"delete_category(hard id={category_id})")
            return DeleteResult(status="hard", usage_count=0)
        con.execute(
            "UPDATE categories SET is_active = FALSE WHERE id = ?",
            [category_id],
        )
        commit_with_bump(con, before, context=f"delete_category(soft id={category_id})")
        return DeleteResult(status="soft", usage_count=usage)
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.delete_category")
        raise
