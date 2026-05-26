"""Catalog business logic: snapshot building, ETag helpers, most-used defaults."""

import contextlib
import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.api.controllers.catalog_writer_errors import AddResult, CatalogWriteError, DeleteResult
from dinary.db.catalog import get_catalog_version
from dinary.sheets.sheet_mapping import decode_auto_tags_value

# ---------------------------------------------------------------------------
# Shared Pydantic models
# ---------------------------------------------------------------------------


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
    auto_tags: list[int]
    is_active: bool
    removable: bool


class TagItem(BaseModel):
    id: int
    name: str
    is_active: bool
    removable: bool


class FrequentCategory(BaseModel):
    id: int
    name: str


class CatalogResponse(BaseModel):
    catalog_version: int
    category_groups: list[CategoryGroupItem]
    categories: list[CategoryItem]
    events: list[EventItem]
    tags: list[TagItem]
    frequent_categories: list[FrequentCategory]


AddStatusLiteral = Literal["created", "reactivated", "noop"]
DeleteStatusLiteral = Literal["hard", "soft"]


class AdminCatalogResponse(BaseModel):
    new_id: int | None = None
    status: AddStatusLiteral | None = None
    delete_status: DeleteStatusLiteral | None = None
    usage_count: int | None = None
    catalog_version: int
    category_groups: list[CategoryGroupItem]
    categories: list[CategoryItem]
    events: list[EventItem]
    tags: list[TagItem]


class ReloadMapResponse(BaseModel):
    row_count: int
    modified_time: str
    tab: str
    modified_time_cached: bool


# Request bodies
class EventAddBody(BaseModel):
    name: str = Field(min_length=1)
    date_from: date
    date_to: date
    auto_attach_enabled: bool = False
    auto_tags: list[int] | None = None


class EventPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    date_from: date | None = None
    date_to: date | None = None
    auto_attach_enabled: bool | None = None
    auto_tags: list[int] | None = None
    is_active: bool | None = None


class TagAddBody(BaseModel):
    name: str = Field(min_length=1)


class TagPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    is_active: bool | None = None


class CategoryAddBody(BaseModel):
    name: str = Field(min_length=1)
    group_id: int
    sheet_name: str | None = None
    sheet_group: str | None = None


class CategoryPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    group_id: int | None = None
    sheet_name: str | None = None
    sheet_group: str | None = None
    is_active: bool | None = None


class GroupAddBody(BaseModel):
    name: str = Field(min_length=1)
    sort_order: int | None = None


class GroupPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    sort_order: int | None = None
    is_active: bool | None = None


# ---------------------------------------------------------------------------
# ETag helpers
# ---------------------------------------------------------------------------


def etag_for(catalog_version: int) -> str:
    return f'W/"catalog-v{catalog_version}"'


def if_none_match_matches(header_value: str, etag: str) -> bool:
    stripped = header_value.strip()
    if not stripped:
        return False
    if stripped == "*":
        return True
    return any(token.strip() == etag for token in stripped.split(","))


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------


def _sum_counts_by_id(
    con: sqlite3.Connection,
    tables_and_cols: tuple[tuple[str, str], ...],
) -> Counter[int]:
    out: Counter[int] = Counter()
    for table, col in tables_and_cols:
        rows = con.execute(
            f"SELECT {col}, COUNT(*) FROM {table} WHERE {col} IS NOT NULL GROUP BY {col}",  # noqa: S608
        ).fetchall()
        for row_id, n in rows:
            out[int(row_id)] += int(n)
    return out


def _auto_tag_refs_by_tag_id(con: sqlite3.Connection) -> Counter[int]:
    id_refs: Counter[int] = Counter()
    rows = con.execute(
        "SELECT auto_tags FROM events"
        " WHERE auto_tags IS NOT NULL AND auto_tags != '' AND auto_tags != '[]'",
    ).fetchall()
    for (raw,) in rows:
        for tag_id in decode_auto_tags_value(raw, context="catalog auto_tags scan"):
            id_refs[tag_id] += 1
    return id_refs


def reference_counts(
    con: sqlite3.Connection,
) -> tuple[dict[int, int], dict[int, int], dict[int, int], dict[int, int]]:
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


_MANUAL_RECENCY = " AND e.receipt_id IS NULL AND e.datetime >= datetime('now', '-3 months')"

_SQL_CAT_DEFAULTS = (
    "SELECT c.group_id, e.category_id, COUNT(*) AS cnt FROM expenses e"  # noqa: S608
    " JOIN categories c ON c.id = e.category_id"
    " WHERE c.is_active = 1"
    + _MANUAL_RECENCY
    + " GROUP BY c.group_id, e.category_id ORDER BY c.group_id, cnt DESC"
)
_SQL_GROUP_DEFAULT = (
    "SELECT c.group_id, COUNT(*) AS cnt FROM expenses e"  # noqa: S608
    " JOIN categories c ON c.id = e.category_id"
    " JOIN category_groups g ON g.id = c.group_id"
    " WHERE g.is_active = 1" + _MANUAL_RECENCY + " GROUP BY c.group_id ORDER BY cnt DESC LIMIT 1"
)


def frequent_categories_sync(con: sqlite3.Connection, limit: int = 5) -> list[FrequentCategory]:
    rows = con.execute(
        """
        SELECT e.category_id, c.name, COUNT(*) AS cnt
          FROM expenses e JOIN categories c ON c.id = e.category_id
         WHERE c.is_active = 1
           AND e.receipt_id IS NULL
           AND e.datetime >= datetime('now', '-3 months')
         GROUP BY e.category_id ORDER BY cnt DESC LIMIT ?
        """,
        [limit],
    ).fetchall()
    return [FrequentCategory(id=int(r[0]), name=str(r[1])) for r in rows]


def most_used_category_per_group(con: sqlite3.Connection) -> dict[int, int]:
    rows = con.execute(_SQL_CAT_DEFAULTS).fetchall()
    result: dict[int, int] = {}
    for group_id, cat_id, _ in rows:
        result.setdefault(int(group_id), int(cat_id))
    return result


def most_used_group(con: sqlite3.Connection) -> int | None:
    row = con.execute(_SQL_GROUP_DEFAULT).fetchone()
    return int(row[0]) if row else None


def build_catalog_snapshot(con: sqlite3.Connection) -> dict[str, Any]:
    version = get_catalog_version(con)
    cat_refs, event_refs, tag_refs, group_children = reference_counts(con)
    freq_cats = frequent_categories_sync(con)

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
                "auto_tags": decode_auto_tags_value(r[5], context=f"event_id={int(r[0])}"),
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
        "frequent_categories": [{"id": fc.id, "name": fc.name} for fc in freq_cats],
    }


def wrap_catalog_error(exc: CatalogWriteError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


@contextlib.contextmanager
def handle_catalog_error() -> Iterator[None]:
    """Re-raise CatalogWriteError as the appropriate HTTPException."""
    try:
        yield
    except CatalogWriteError as exc:
        raise wrap_catalog_error(exc) from None


def snapshot_response(
    con: sqlite3.Connection,
    etag_fn: Any,
    add_result: AddResult | None = None,
    delete_result: DeleteResult | None = None,
) -> tuple[AdminCatalogResponse, str]:
    snapshot = build_catalog_snapshot(con)
    etag = etag_fn(snapshot["catalog_version"])
    return (
        AdminCatalogResponse(
            new_id=add_result.id if add_result else None,
            status=add_result.status if add_result else None,
            delete_status=delete_result.status if delete_result else None,
            usage_count=delete_result.usage_count if delete_result else None,
            **snapshot,
        ),
        etag,
    )
