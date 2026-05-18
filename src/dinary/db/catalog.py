"""Catalog read queries, mapping resolution, and sheet-logging projection.

All functions accept an open ``sqlite3.Connection`` (from ``db.get_connection()``)
and are read-only except ``set_catalog_version``.
"""

import json
import sqlite3

from dinary.db.sql_loader import fetchall_as, fetchone_as, load_sql
from dinary.db.storage import (
    CategoryListRow,
    LoggingProjectionCandidateRow,
    MappingRow,
)

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
