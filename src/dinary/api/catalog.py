"""GET /api/catalog — one-shot, ETag-cacheable catalog snapshot.

Single endpoint supplying the PWA's full 3D taxonomy (groups,
categories, events, tags) on the hot path. Payload:

    {
      catalog_version: int,
      categories:       [{id, name, group, group_id, is_active, removable}],
      category_groups:  [{id, name, sort_order, is_active, removable}],
      events:           [{id, name, date_from, date_to,
                          auto_attach_enabled, auto_tags, is_active,
                          removable}],
      tags:             [{id, name, is_active, removable}],
    }

``removable`` is the server-precomputed answer to "would a DELETE
on this row hard-delete (i.e. succeed and actually drop the row)?".
The flag is ``true`` only when the row is not referenced by any FK
into it from ``expenses`` / ``expense_tags`` nor by any mapping
table (``sheet_mapping`` / ``sheet_mapping_tags`` / ``import_mapping``
/ ``import_mapping_tags``), and — for tags — no event's
``auto_tags`` JSON payload contains the tag's name. The PWA uses it
to hide the "Удалить" button on rows that would soft-delete
anyway, keeping the management list honest.

ETag is ``W/"catalog-v<N>"`` where N is ``catalog_version``. It rides
on the HTTP ``ETag`` response header only — the body does **not**
carry a duplicate ``etag`` field because the value is a pure function
of ``catalog_version`` and the PWA can derive it client-side. Clients
send ``If-None-Match`` on subsequent polls; a match returns ``304 Not
Modified`` with no body. Coupled with the PWA's catalog cache + the
``catalog_version`` bump emitted from every ``POST /api/expenses``
response, the steady state is **zero catalog GETs per expense**.

All catalog items — including ``is_active=FALSE`` rows retired from
the live taxonomy — are returned; every row carries an ``is_active``
flag. The PWA filters client-side by default but exposes per-picker
"show inactive" toggles and a reactivate affordance, so soft-deleted
categories / tags / events never disappear from the UI before the
operator has a chance to un-retire them. Events additionally carry
``auto_tags`` (the tag-name list auto-unioned into every expense that
attaches the event, e.g. ``["отпуск", "путешествия"]`` on vacation
events).
"""

import logging
import sqlite3
from collections import Counter

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel

from dinary.services import ledger_repo
from dinary.services.sheet_mapping import decode_auto_tags_value

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryGroupItem(BaseModel):
    id: int
    name: str
    sort_order: int
    is_active: bool
    removable: bool


class CategoryItem(BaseModel):
    id: int
    name: str
    group: str
    group_id: int
    is_active: bool
    removable: bool


class EventItem(BaseModel):
    id: int
    name: str
    date_from: str
    date_to: str
    auto_attach_enabled: bool
    auto_tags: list[str]
    is_active: bool
    removable: bool


class TagItem(BaseModel):
    id: int
    name: str
    is_active: bool
    removable: bool


class CatalogResponse(BaseModel):
    catalog_version: int
    category_groups: list[CategoryGroupItem]
    categories: list[CategoryItem]
    events: list[EventItem]
    tags: list[TagItem]


def _etag_for(catalog_version: int) -> str:
    """Canonical ETag string for a given ``catalog_version``.

    Mirrored in the PWA (``static/js/api.js::etagFor``) so the client
    can derive the ETag it needs to send in ``If-None-Match`` without
    us having to ship the string in the response body. Any change
    here must stay in lockstep with the client copy.
    """
    return f'W/"catalog-v{catalog_version}"'


def _if_none_match_matches(header_value: str, etag: str) -> bool:
    """RFC 7232-compliant ``If-None-Match`` match against a single ETag."""
    stripped = header_value.strip()
    if not stripped:
        return False
    if stripped == "*":
        return True
    return any(token.strip() == etag for token in stripped.split(","))


def _sum_counts_by_id(
    con: sqlite3.Connection,
    tables_and_cols: tuple[tuple[str, str], ...],
) -> Counter[int]:
    """Union of ``SELECT col, COUNT(*) ... GROUP BY col`` across tables.

    Each (table, col) pair contributes one aggregate query; results
    accumulate into a single ``Counter`` keyed by row id. ``col IS
    NULL`` rows are excluded (they don't reference anything).
    """
    out: Counter[int] = Counter()
    for table, col in tables_and_cols:
        rows = con.execute(
            f"SELECT {col}, COUNT(*) FROM {table} WHERE {col} IS NOT NULL GROUP BY {col}",  # noqa: S608
        ).fetchall()
        for row_id, n in rows:
            out[int(row_id)] += int(n)
    return out


def _auto_tag_refs_by_tag_id(con: sqlite3.Connection) -> Counter[int]:
    """Count events whose ``auto_tags`` JSON array contains each tag name.

    ``events.auto_tags`` stores tag names (not ids), so SQLite's FK
    engine misses it. Scan once, build a name->count Counter, then
    translate to ids via the ``tags`` table. Cheap: the events table
    is small.
    """
    name_refs: Counter[str] = Counter()
    rows = con.execute(
        "SELECT auto_tags FROM events"
        " WHERE auto_tags IS NOT NULL AND auto_tags != '' AND auto_tags != '[]'",
    ).fetchall()
    for (raw,) in rows:
        for name in decode_auto_tags_value(raw, context="catalog auto_tags scan"):
            name_refs[name] += 1
    if not name_refs:
        return Counter()
    id_refs: Counter[int] = Counter()
    for tag_id, tag_name in con.execute("SELECT id, name FROM tags").fetchall():
        hits = name_refs.get(str(tag_name), 0)
        if hits:
            id_refs[int(tag_id)] = hits
    return id_refs


def _reference_counts(
    con: sqlite3.Connection,
) -> tuple[dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
    """Aggregate FK/reference counts needed for the ``removable`` flag.

    Returns four dicts keyed by row id: total-refs-per-category-id,
    total-refs-per-event-id, total-refs-per-tag-id (including
    ``events.auto_tags`` name matches), and total-child-count-per-
    group-id. One aggregate query per reference table keeps this at
    O(tables), not O(rows). The predicates (mapping tables + expense
    FKs + events.auto_tags scan) mirror
    ``catalog_writer._*_mapping_reference_count`` /
    ``_events_auto_tags_reference_count`` so ``removable=true``
    corresponds exactly to "hard-delete would succeed".
    """
    cat_refs = _sum_counts_by_id(
        con,
        (
            ("expenses", "category_id"),
            ("sheet_mapping", "category_id"),
            ("import_mapping", "category_id"),
        ),
    )
    event_refs = _sum_counts_by_id(
        con,
        (
            ("expenses", "event_id"),
            ("sheet_mapping", "event_id"),
            ("import_mapping", "event_id"),
        ),
    )
    tag_refs = _sum_counts_by_id(
        con,
        (
            ("expense_tags", "tag_id"),
            ("sheet_mapping_tags", "tag_id"),
            ("import_mapping_tags", "tag_id"),
        ),
    )
    tag_refs.update(_auto_tag_refs_by_tag_id(con))

    group_child_counts: dict[int, int] = {}
    for row_id, n in con.execute(
        "SELECT group_id, COUNT(*) FROM categories GROUP BY group_id",
    ).fetchall():
        group_child_counts[int(row_id)] = int(n)

    return dict(cat_refs), dict(event_refs), dict(tag_refs), group_child_counts


def build_catalog_snapshot(con: sqlite3.Connection) -> dict:
    """Shared by GET /api/catalog and the admin POST/PATCH responses.

    Returns a dict-of-lists suitable for embedding directly in a
    pydantic response model (``CatalogResponse`` / ``AdminCatalogResponse``).
    Returns **all** rows regardless of ``is_active``; the flag is
    surfaced per-row so the PWA can render and reactivate inactive
    items without hitting a separate endpoint. Each row also carries
    a ``removable`` boolean indicating whether ``DELETE`` would
    hard-delete (true) or soft-delete (false) the row today — the
    PWA uses it to hide the ``Удалить`` button on still-referenced
    rows.
    """
    version = ledger_repo.get_catalog_version(con)
    cat_refs, event_refs, tag_refs, group_children = _reference_counts(con)

    group_rows = con.execute(
        "SELECT id, name, sort_order, is_active FROM category_groups ORDER BY sort_order, id",
    ).fetchall()

    category_rows = con.execute(
        "SELECT c.id, c.name, c.group_id, g.name, c.is_active"
        " FROM categories c JOIN category_groups g ON g.id = c.group_id"
        " ORDER BY g.sort_order, c.name",
    ).fetchall()

    event_rows = con.execute(
        "SELECT id, name, date_from, date_to, auto_attach_enabled,"
        " auto_tags, is_active"
        " FROM events ORDER BY date_from, name",
    ).fetchall()

    tag_rows = con.execute(
        "SELECT id, name, is_active FROM tags ORDER BY id",
    ).fetchall()

    return {
        "catalog_version": version,
        "category_groups": [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "sort_order": int(r[2]),
                "is_active": bool(r[3]),
                "removable": group_children.get(int(r[0]), 0) == 0,
            }
            for r in group_rows
        ],
        "categories": [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "group_id": int(r[2]),
                "group": str(r[3]),
                "is_active": bool(r[4]),
                "removable": cat_refs.get(int(r[0]), 0) == 0,
            }
            for r in category_rows
        ],
        "events": [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "date_from": r[2].isoformat() if hasattr(r[2], "isoformat") else str(r[2]),
                "date_to": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
                "auto_attach_enabled": bool(r[4]),
                "auto_tags": decode_auto_tags_value(
                    r[5],
                    context=f"event_id={int(r[0])}",
                ),
                "is_active": bool(r[6]),
                "removable": event_refs.get(int(r[0]), 0) == 0,
            }
            for r in event_rows
        ],
        "tags": [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "is_active": bool(r[2]),
                "removable": tag_refs.get(int(r[0]), 0) == 0,
            }
            for r in tag_rows
        ],
    }


@router.get("/api/catalog", response_model=None)
def get_catalog(
    response: Response,
    if_none_match: str | None = Header(default=None),
) -> CatalogResponse | Response:
    try:
        con = ledger_repo.get_connection()
        try:
            snapshot = build_catalog_snapshot(con)
        finally:
            con.close()
    except Exception:
        logger.exception("Failed to load catalog snapshot")
        raise HTTPException(status_code=500, detail="Failed to load catalog") from None

    etag = _etag_for(snapshot["catalog_version"])
    if if_none_match is not None and _if_none_match_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return CatalogResponse(**snapshot)
