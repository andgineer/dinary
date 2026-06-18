"""Catalog read queries, mapping resolution, and sheet-logging projection.

All functions accept an open ``sqlite3.Connection`` (from ``db.get_connection()``)
and are read-only except ``set_catalog_version``.
"""

import json
import re
import sqlite3

from dinary.db import storage
from dinary.db.sql_loader import fetchall_as, fetchone_as, load_sql
from dinary.db.storage import (
    LoggingProjectionCandidateRow,
    MappingRow,
    VisibleCategoryRow,
)

#: The "pickable for new expenses" predicate (specs/reference/category-templates.md).
#: ``is_active`` already reflects template membership and historical use:
#: ``apply_template`` is the only writer that can deactivate a category during
#: normal operation, and it never does so for one with expenses.
#: (``category_seed``'s retirement step also clears ``is_active``, but always
#: alongside ``is_retired``, which this predicate excludes unconditionally.)
#: Assumes the categories table is aliased ``c`` in the enclosing query.
VISIBLE_CATEGORY_PREDICATE = "NOT c.is_retired AND NOT c.is_hidden AND c.is_active"


# ---------------------------------------------------------------------------
# Catalog queries
# ---------------------------------------------------------------------------


def list_visible_categories(con: sqlite3.Connection) -> list[VisibleCategoryRow]:
    """Return the pickable category set, grouped and ordered for the picker."""
    return fetchall_as(VisibleCategoryRow, con, load_sql("list_visible_categories.sql"))


def get_active_template(con: sqlite3.Connection) -> str | None:
    row = con.execute(
        "SELECT value FROM app_metadata WHERE key = 'active_template'",
    ).fetchone()
    return str(row[0]) if row is not None else None


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

    Callers: ``catalog_writer._commit_with_bump`` (the admin-API path),
    ``category_apply.apply_template``, and the category-ops writers below
    (``activate_category``, ``hide_category``, ``unhide_category``,
    ``move_category``, ``create_category``, ``rename_category``). Every other
    module is expected to go through one of those.
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
# Category operations (search, activate, hide, move, create, rename)
# ---------------------------------------------------------------------------


def _require_category(con: sqlite3.Connection, code: str) -> None:
    row = con.execute("SELECT 1 FROM categories WHERE code = ?", [code]).fetchone()
    if row is None:
        msg = f"Unknown category code: {code!r}"
        raise ValueError(msg)


def _resolve_group_code_in_template(definition: dict, code: str) -> str | None:
    """Find which group ``code`` sits in within a template's ``visible``/``hidden``."""
    for bucket in ("visible", "hidden"):
        for group_code, codes in definition[bucket].items():
            if code in codes:
                return group_code
    return None


def activate_category(con: sqlite3.Connection, code: str) -> None:
    """Make ``code`` pickable: ``is_active=1, is_hidden=0``.

    If ``group_id`` is ``NULL``, resolve it from the active template's
    definition. Raises ``ValueError`` if the code is unknown, or if
    ``group_id`` is ``NULL`` and no group can be resolved (no active
    template, or the template row is missing) — ``is_active=1`` with
    ``group_id=NULL`` is never a valid catalog state.
    """
    with storage.transaction(con):
        row = con.execute(
            "SELECT group_id FROM categories WHERE code = ?",
            [code],
        ).fetchone()
        if row is None:
            msg = f"Unknown category code: {code!r}"
            raise ValueError(msg)

        if row["group_id"] is None:
            template_code = get_active_template(con)
            template_row = (
                con.execute(
                    "SELECT definition_json FROM category_templates WHERE code = ?",
                    [template_code],
                ).fetchone()
                if template_code is not None
                else None
            )
            group_code = (
                _resolve_group_code_in_template(json.loads(template_row["definition_json"]), code)
                if template_row is not None
                else None
            )
            if group_code is None:
                msg = f"Cannot activate {code!r}: no group could be resolved"
                raise ValueError(msg)
            con.execute(
                "UPDATE categories SET group_id = "
                "(SELECT id FROM category_groups WHERE code = ?) WHERE code = ?",
                [group_code, code],
            )

        con.execute(
            "UPDATE categories SET is_active = 1, is_hidden = 0 WHERE code = ?",
            [code],
        )
        set_catalog_version(con, get_catalog_version(con) + 1)


def hide_category(con: sqlite3.Connection, code: str) -> None:
    """Set ``is_hidden=1``. Sticky: only an explicit ``activate_category`` clears it."""
    with storage.transaction(con):
        _require_category(con, code)
        con.execute("UPDATE categories SET is_hidden = 1 WHERE code = ?", [code])
        set_catalog_version(con, get_catalog_version(con) + 1)


def unhide_category(con: sqlite3.Connection, code: str) -> None:
    """Clear ``is_hidden`` without touching ``is_active``.

    If the category is also inactive and has no expenses, it remains
    invisible in ``list_visible_categories`` until activated.
    """
    with storage.transaction(con):
        _require_category(con, code)
        con.execute("UPDATE categories SET is_hidden = 0 WHERE code = ?", [code])
        set_catalog_version(con, get_catalog_version(con) + 1)


def move_category(con: sqlite3.Connection, code: str, group_code: str) -> None:
    """Set ``group_id`` to a manual override. Raises ``ValueError`` if either code is unknown."""
    with storage.transaction(con):
        _require_category(con, code)
        group_row = con.execute(
            "SELECT id FROM category_groups WHERE code = ?",
            [group_code],
        ).fetchone()
        if group_row is None:
            msg = f"Unknown category group code: {group_code!r}"
            raise ValueError(msg)
        con.execute(
            "UPDATE categories SET group_id = ? WHERE code = ?",
            [group_row["id"], code],
        )
        set_catalog_version(con, get_catalog_version(con) + 1)


def _slugify(name: str) -> str:
    """Lowercase ``name``, replace runs of non-alphanumerics with ``_``, strip edges."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "category"


def create_category(con: sqlite3.Connection, name: str, group_code: str) -> str:
    """Create a new ``u_``-prefixed category, immediately active. Returns its code.

    Raises ``ValueError`` if ``group_code`` is unknown.
    """
    with storage.transaction(con):
        group_row = con.execute(
            "SELECT id FROM category_groups WHERE code = ?",
            [group_code],
        ).fetchone()
        if group_row is None:
            msg = f"Unknown category group code: {group_code!r}"
            raise ValueError(msg)

        slug = _slugify(name)
        code = f"u_{slug}"
        suffix = 2
        while con.execute("SELECT 1 FROM categories WHERE code = ?", [code]).fetchone():
            code = f"u_{slug}_{suffix}"
            suffix += 1

        con.execute(
            "INSERT INTO categories (name, group_id, is_active, code, is_hidden, is_retired) "
            "VALUES (?, ?, 1, ?, 0, 0)",
            [name, group_row["id"], code],
        )
        set_catalog_version(con, get_catalog_version(con) + 1)

    return code


def rename_category(con: sqlite3.Connection, code: str, name: str) -> None:
    """Set ``name`` only; ``code`` stays stable."""
    with storage.transaction(con):
        _require_category(con, code)
        con.execute("UPDATE categories SET name = ? WHERE code = ?", [name, code])
        set_catalog_version(con, get_catalog_version(con) + 1)


# ---------------------------------------------------------------------------
# Sheet mapping resolution (import path)
# ---------------------------------------------------------------------------


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
