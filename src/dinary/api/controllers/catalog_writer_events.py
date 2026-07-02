"""Events and tags CRUD.

Shared write primitives live in ``catalog_writer``.
Exception types and result dataclasses live in ``catalog_writer_errors``.
"""

import json
import re
import sqlite3
from datetime import date

from dinary.api.controllers.catalog_writer import commit_with_bump, hash_state
from dinary.api.controllers.catalog_writer_errors import (
    AddResult,
    AddStatus,
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogWriteError,
    DeleteResult,
)
from dinary.db import storage
from dinary.sheets.sheet_mapping import decode_auto_tags_value

# ---------------------------------------------------------------------------
# Tag-name validation
# ---------------------------------------------------------------------------

#: Rejected because the ``map`` tab's tags cell is comma/whitespace-separated —
#: a tag containing either would silently route expenses into the wrong envelope.
_DISALLOWED_TAG_NAME_RE = re.compile(r"[,\s]")


def _validate_tag_name(name: str) -> None:
    if _DISALLOWED_TAG_NAME_RE.search(name):
        raise CatalogWriteError(
            f"tag name {name!r} contains whitespace or ','; the map tab uses "
            "these as separators so they cannot appear inside a single name",
            http_status=422,
        )


def _require_known_tag_ids(
    con: sqlite3.Connection,
    tag_ids: list[int] | tuple[int, ...],
) -> None:
    unique = sorted({int(t) for t in tag_ids})
    if not unique:
        return
    placeholders = ",".join(["?"] * len(unique))
    rows = con.execute(
        f"SELECT id FROM tags WHERE id IN ({placeholders})",  # noqa: S608
        unique,
    ).fetchall()
    found = {int(r[0]) for r in rows}
    missing = [t for t in unique if t not in found]
    if missing:
        raise CatalogWriteError(
            f"auto_tags references unknown tag id(s) {missing}; create the tag first",
            http_status=422,
        )


# ---------------------------------------------------------------------------
# Usage-count and mapping-reference helpers
# ---------------------------------------------------------------------------


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


def _event_mapping_reference_count(con: sqlite3.Connection, event_id: int) -> int:
    row = con.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM sheet_mapping WHERE event_id = ?) "
        " + (SELECT COUNT(*) FROM import_mapping WHERE event_id = ?)",
        [event_id, event_id],
    ).fetchone()
    return int(row[0]) if row else 0


def _events_auto_tags_reference_count(con: sqlite3.Connection, tag_id: int) -> int:
    rows = con.execute(
        "SELECT id, auto_tags FROM events"
        " WHERE auto_tags IS NOT NULL AND auto_tags != '' AND auto_tags != '[]'",
    ).fetchall()
    count = 0
    for event_id, raw in rows:
        decoded = decode_auto_tags_value(raw, context=f"event_id={int(event_id)}")
        if tag_id in decoded:
            count += 1
    return count


def _tag_mapping_reference_count(con: sqlite3.Connection, tag_id: int) -> int:
    auto_tag_refs = _events_auto_tags_reference_count(con, tag_id)
    row = con.execute(
        "SELECT "
        " (SELECT COUNT(*) FROM sheet_mapping_tags WHERE tag_id = ?) "
        " + (SELECT COUNT(*) FROM import_mapping_tags WHERE tag_id = ?)",
        [tag_id, tag_id],
    ).fetchone()
    mapping_refs = int(row[0]) if row else 0
    return mapping_refs + auto_tag_refs


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _encode_auto_tags(auto_tags: list[int] | tuple[int, ...] | None) -> str:
    ids = list(dict.fromkeys(auto_tags)) if auto_tags is not None else []
    return json.dumps(ids)


def add_event(
    con: sqlite3.Connection,
    *,
    name: str,
    date_from: date,
    date_to: date,
    auto_attach_enabled: bool = False,
    auto_tags: list[int] | None = None,
) -> AddResult:
    """Create a new event, or reactivate-in-place if the name exists (existing
    dates/auto_tags are left untouched on reactivate — use ``edit_event`` to change
    them). Validates the input regardless, even though the reactivate path discards
    it, so a garbage body always surfaces as 422 rather than being silently dropped."""
    if date_from > date_to:
        raise CatalogWriteError(
            f"event date_from ({date_from}) must be <= date_to ({date_to})",
            http_status=422,
        )
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
        _require_known_tag_ids(con, auto_tags or [])
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
            bumped = commit_with_bump(
                con,
                before,
                context=f"add_event(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=eid, status=status)
        eid = con.execute(
            "INSERT INTO events"
            " (name, date_from, date_to, auto_attach_enabled, is_active, auto_tags)"
            " VALUES (?, ?, ?, ?, TRUE, ?) RETURNING id",
            [
                name,
                date_from,
                date_to,
                auto_attach_enabled,
                _encode_auto_tags(auto_tags),
            ],
        ).fetchone()[0]
        commit_with_bump(con, before, context=f"add_event(name={name!r})")
        return AddResult(id=eid, status="created")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.add_event")
        raise


def _validate_event_edit(
    con: sqlite3.Connection,
    event_id: int,
    name: str | None,
    dates: tuple[date | None, date | None],
    auto_tags: list[int] | None,
) -> None:
    row = con.execute(
        "SELECT id, date_from, date_to FROM events WHERE id = ?",
        [event_id],
    ).fetchone()
    if row is None:
        raise CatalogNotFoundError(f"event id={event_id} not found")
    date_from, date_to = dates
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
    if auto_tags is not None:
        _require_known_tag_ids(con, auto_tags)


def edit_event(
    con: sqlite3.Connection,
    event_id: int,
    *,
    name: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    auto_attach_enabled: bool | None = None,
    auto_tags: list[int] | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH: ``auto_tags=None`` leaves the column alone, an empty list
    clears it. All validations run before any UPDATE so a failure never leaves
    the row half-edited; the date-range check merges patch values onto the
    current row so patching only one of ``date_from``/``date_to`` still validates."""
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
        _validate_event_edit(con, event_id, name, (date_from, date_to), auto_tags)
        if name is not None:
            con.execute("UPDATE events SET name = ? WHERE id = ?", [name, event_id])
        if date_from is not None:
            con.execute("UPDATE events SET date_from = ? WHERE id = ?", [date_from, event_id])
        if date_to is not None:
            con.execute("UPDATE events SET date_to = ? WHERE id = ?", [date_to, event_id])
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
        commit_with_bump(con, before, context=f"edit_event(id={event_id})")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.edit_event")
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
    _validate_tag_name(name)
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
        existing = con.execute(
            "SELECT id, is_active FROM tags WHERE name = ?",
            [name],
        ).fetchone()
        if existing is not None:
            tid = int(existing[0])
            was_active = bool(existing[1])
            if not was_active:
                con.execute("UPDATE tags SET is_active = TRUE WHERE id = ?", [tid])
            bumped = commit_with_bump(
                con,
                before,
                context=f"add_tag(reactivate name={name!r})",
            )
            status: AddStatus = "reactivated" if bumped else "noop"
            return AddResult(id=tid, status=status)
        tid = con.execute(
            "INSERT INTO tags (name, is_active) VALUES (?, TRUE) RETURNING id",
            [name],
        ).fetchone()[0]
        commit_with_bump(con, before, context=f"add_tag(name={name!r})")
        return AddResult(id=tid, status="created")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.add_tag")
        raise


def edit_tag(
    con: sqlite3.Connection,
    tag_id: int,
    *,
    name: str | None = None,
    is_active: bool | None = None,
) -> None:
    """Atomic PATCH: validations run before any UPDATE so a failure never leaves
    the row half-edited."""
    if name is not None:
        _validate_tag_name(name)
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
        row = con.execute("SELECT id FROM tags WHERE id = ?", [tag_id]).fetchone()
        if row is None:
            raise CatalogNotFoundError(f"tag id={tag_id} not found")
        if name is not None:
            conflict = con.execute(
                "SELECT id FROM tags WHERE name = ? AND id != ?",
                [name, tag_id],
            ).fetchone()
            if conflict is not None:
                raise CatalogConflictError(
                    f"tag name {name!r} already in use by id={int(conflict[0])}",
                )
            con.execute("UPDATE tags SET name = ? WHERE id = ?", [name, tag_id])
        if is_active is not None:
            con.execute(
                "UPDATE tags SET is_active = ? WHERE id = ?",
                [bool(is_active), tag_id],
            )
        commit_with_bump(con, before, context=f"edit_tag(id={tag_id})")
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.edit_tag")
        raise


def set_tag_active(
    con: sqlite3.Connection,
    tag_id: int,
    active: bool,
) -> None:
    """Thin wrapper around ``edit_tag(is_active=...)``; kept for test readability."""
    edit_tag(con, tag_id, is_active=active)


def delete_event(
    con: sqlite3.Connection,
    event_id: int,
) -> DeleteResult:
    """Hard-delete iff nothing references this event; otherwise flip
    ``is_active=FALSE``.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
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
            commit_with_bump(con, before, context=f"delete_event(hard id={event_id})")
            return DeleteResult(status="hard", usage_count=0)
        con.execute(
            "UPDATE events SET is_active = FALSE WHERE id = ?",
            [event_id],
        )
        commit_with_bump(con, before, context=f"delete_event(soft id={event_id})")
        return DeleteResult(status="soft", usage_count=usage)
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.delete_event")
        raise


def delete_tag(
    con: sqlite3.Connection,
    tag_id: int,
) -> DeleteResult:
    """Hard-delete iff nothing references this tag (``expense_tags``,
    ``sheet_mapping_tags``, ``import_mapping_tags``); otherwise flip ``is_active=FALSE``."""
    con.execute("BEGIN IMMEDIATE")
    try:
        before = hash_state(con)
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
            commit_with_bump(con, before, context=f"delete_tag(hard id={tag_id})")
            return DeleteResult(status="hard", usage_count=0)
        con.execute(
            "UPDATE tags SET is_active = FALSE WHERE id = ?",
            [tag_id],
        )
        commit_with_bump(con, before, context=f"delete_tag(soft id={tag_id})")
        return DeleteResult(status="soft", usage_count=usage)
    except Exception:
        storage.best_effort_rollback(con, context="catalog_writer.delete_tag")
        raise
