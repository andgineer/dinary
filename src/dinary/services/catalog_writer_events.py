"""Events and tags CRUD — split from catalog_writer to keep file sizes manageable.

All shared helpers, exception classes, and state-hashing utilities live in
``services.catalog_writer`` and are imported from there.
"""

import json
import sqlite3
from datetime import date

from dinary.services import ledger_repo
from dinary.services.catalog_writer import (
    AddResult,
    AddStatus,
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogWriteError,
    DeleteResult,
    _commit_with_bump,
    _event_mapping_reference_count,
    _event_usage_count,
    _hash_state,
    _next_id,
    _require_known_tag_names,
    _tag_mapping_reference_count,
    _tag_usage_count,
    _validate_tag_name,
)
from dinary.services.sheet_mapping import decode_auto_tags_value

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


def add_event(
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


def _validate_event_edit(
    con: sqlite3.Connection,
    event_id: int,
    name: str | None,
    dates: tuple[date | None, date | None],
    auto_tags,
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
        _require_known_tag_names(con, auto_tags)


def edit_event(
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
