"""Category groups CRUD.

Shared helpers, exception classes, and state-hashing utilities live in
``services.catalog_writer`` and are imported from there.
"""

import sqlite3

from dinary.api.controllers.catalog_writer import commit_with_bump, hash_state, next_id
from dinary.api.controllers.catalog_writer_errors import (
    AddResult,
    AddStatus,
    CatalogConflictError,
    CatalogInUseError,
    CatalogNotFoundError,
    DeleteResult,
)
from dinary.db import storage


def _group_usage_count(con: sqlite3.Connection, group_id: int) -> int:
    """Active-only count for ``edit_group``, unlike :func:`_group_child_category_count`
    (used by ``delete_group``) which counts all children — the FK doesn't care about
    ``is_active``, but deactivating a group shouldn't be blocked by already-hidden ones."""
    row = con.execute(
        "SELECT COUNT(*) FROM categories WHERE group_id = ? AND is_active",
        [group_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _group_child_category_count(
    con: sqlite3.Connection,
    group_id: int,
) -> int:
    """Gates ``delete_group``: a non-zero count means the FK would reject the DELETE."""
    row = con.execute(
        "SELECT COUNT(*) FROM categories WHERE group_id = ?",
        [group_id],
    ).fetchone()
    return int(row[0]) if row else 0


def add_group(
    con: sqlite3.Connection,
    *,
    name: str,
    sort_order: int | None = None,
) -> AddResult:
    """Create a new category group, or reactivate-in-place if the name exists.

    Reactivate preserves the existing ``sort_order`` (see
    ``catalog_writer`` module docstring for the reactivate contract).
    To change ``sort_order``, use ``edit_group``.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
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
            bumped = commit_with_bump(
                con,
                before,
                context=f"add_group(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=gid, status=status)
        gid = next_id(con, "category_groups")
        if sort_order is None:
            row = con.execute(
                "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM category_groups",
            ).fetchone()
            sort_order = int(row[0]) if row else 1
        con.execute(
            "INSERT INTO category_groups (id, name, sort_order, is_active) VALUES (?, ?, ?, TRUE)",
            [gid, name, sort_order],
        )
        commit_with_bump(con, before, context=f"add_group(name={name!r})")
        return AddResult(id=gid, status="created")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.add_group")
        raise


def edit_group(
    con: sqlite3.Connection,
    group_id: int,
    *,
    name: str | None = None,
    sort_order: int | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH: validations (not-found, conflict, in-use) run before any
    UPDATE so a failure never leaves the row half-edited."""
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
        row = con.execute(
            "SELECT id FROM category_groups WHERE id = ?",
            [group_id],
        ).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"category_group id={group_id} not found")
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
        commit_with_bump(con, before, context=f"edit_group(id={group_id})")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.edit_group")
        raise


def set_group_active(
    con: sqlite3.Connection,
    group_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_group(is_active=...)``; kept for test readability."""
    edit_group(con, group_id, is_active=active)


def delete_group(
    con: sqlite3.Connection,
    group_id: int,
) -> DeleteResult:
    """Hard-delete iff no categories reference this group; raise 409 otherwise
    (stricter than other catalog kinds, see ``specs/reference/catalog-api.md``)."""
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
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
        commit_with_bump(con, before, context=f"delete_group(id={group_id})")
        return DeleteResult(status="hard", usage_count=0)
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.delete_group")
        raise
