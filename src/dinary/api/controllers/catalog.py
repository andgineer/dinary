"""Catalog business logic: snapshot building, ETag helpers, most-used defaults."""

import contextlib
import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import date
from typing import Any, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.api.controllers.catalog_writer_errors import CatalogWriteError
from dinary.db.catalog import VISIBLE_CATEGORY_PREDICATE, get_catalog_version
from dinary.sheets.sheet_mapping import decode_auto_tags_value

# ---------------------------------------------------------------------------
# Shared Pydantic models
# ---------------------------------------------------------------------------


class CategoryGroupItem(BaseModel):
    id: int
    code: str
    name: str
    sort_order: int
    is_active: bool
    removable: bool


class CategoryItem(BaseModel):
    id: int
    code: str
    name: str
    group: str
    group_id: int
    is_active: bool
    is_hidden: bool
    is_retired: bool
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


class CatalogVersionResponse(BaseModel):
    catalog_version: int


class CategoryResultResponse(CatalogVersionResponse):
    category: CategoryItem


class AdminMutationResponse(CatalogVersionResponse):
    delete_status: DeleteStatusLiteral | None = None
    usage_count: int | None = None


class GroupAddResponse(CatalogVersionResponse):
    status: AddStatusLiteral
    group: CategoryGroupItem


class EventAddResponse(CatalogVersionResponse):
    status: AddStatusLiteral
    event: EventItem


class TagAddResponse(CatalogVersionResponse):
    status: AddStatusLiteral
    tag: TagItem


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
        "SELECT group_id, COUNT(*) FROM categories WHERE group_id IS NOT NULL GROUP BY group_id",
    ).fetchall():
        group_child_counts[int(row_id)] = int(n)
    return dict(cat_refs), dict(event_refs), dict(tag_refs), group_child_counts


def _ref_count(
    con: sqlite3.Connection,
    row_id: int,
    tables_and_cols: tuple[tuple[str, str], ...],
) -> int:
    total = 0
    for table, col in tables_and_cols:
        (n,) = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", [row_id]).fetchone()  # noqa: S608
        total += n
    return total


_CATEGORY_REF_TABLES = (
    ("expenses", "category_id"),
    ("sheet_mapping", "category_id"),
    ("import_mapping", "category_id"),
)
_GROUP_REF_TABLES = (("categories", "group_id"),)
_EVENT_REF_TABLES = (
    ("expenses", "event_id"),
    ("sheet_mapping", "event_id"),
    ("import_mapping", "event_id"),
)
_TAG_REF_TABLES = (
    ("expense_tags", "tag_id"),
    ("sheet_mapping_tags", "tag_id"),
    ("import_mapping_tags", "tag_id"),
)


def _category_item(con: sqlite3.Connection, code: str) -> CategoryItem:
    row = con.execute(
        "SELECT c.id, c.code, c.name, c.group_id, g.name AS group_name,"
        " c.is_active, c.is_hidden, c.is_retired"
        " FROM categories c JOIN category_groups g ON g.id = c.group_id"
        " WHERE c.code = ?",
        [code],
    ).fetchone()
    return CategoryItem(
        id=int(row[0]),
        code=str(row[1]),
        name=str(row[2]),
        group_id=int(row[3]),
        group=str(row[4]),
        is_active=bool(row[5]),
        is_hidden=bool(row[6]),
        is_retired=bool(row[7]),
        removable=_ref_count(con, int(row[0]), _CATEGORY_REF_TABLES) == 0,
    )


def _group_item(con: sqlite3.Connection, group_id: int) -> CategoryGroupItem:
    row = con.execute(
        "SELECT id, code, name, sort_order, is_active FROM category_groups WHERE id = ?",
        [group_id],
    ).fetchone()
    return CategoryGroupItem(
        id=int(row[0]),
        code=str(row[1]),
        name=str(row[2]),
        sort_order=int(row[3]),
        is_active=bool(row[4]),
        removable=_ref_count(con, group_id, _GROUP_REF_TABLES) == 0,
    )


def _event_item(con: sqlite3.Connection, event_id: int) -> EventItem:
    row = con.execute(
        "SELECT id, name, date_from, date_to, auto_attach_enabled, auto_tags, is_active"
        " FROM events WHERE id = ?",
        [event_id],
    ).fetchone()
    return EventItem(
        id=int(row[0]),
        name=str(row[1]),
        date_from=str(row[2]),
        date_to=str(row[3]),
        auto_attach_enabled=bool(row[4]),
        auto_tags=decode_auto_tags_value(row[5], context="event add response"),
        is_active=bool(row[6]),
        removable=_ref_count(con, event_id, _EVENT_REF_TABLES) == 0,
    )


def _tag_item(con: sqlite3.Connection, tag_id: int) -> TagItem:
    row = con.execute("SELECT id, name, is_active FROM tags WHERE id = ?", [tag_id]).fetchone()
    return TagItem(
        id=int(row[0]),
        name=str(row[1]),
        is_active=bool(row[2]),
        removable=_ref_count(con, tag_id, _TAG_REF_TABLES) == 0,
    )


_MANUAL_RECENCY = "e.receipt_id IS NULL AND e.datetime >= datetime('now', '-3 months')"

_SQL_CAT_DEFAULTS = (
    "SELECT c.group_id, e.category_id, COUNT(*) AS cnt FROM expenses e"  # noqa: S608
    " JOIN categories c ON c.id = e.category_id"
    f" WHERE {VISIBLE_CATEGORY_PREDICATE} AND {_MANUAL_RECENCY}"
    " GROUP BY c.group_id, e.category_id ORDER BY c.group_id, cnt DESC"
)
# category_groups.is_active is vestigial here: apply_template rewrites every
# category's group_id to a group the active template declares, so a visible
# category can never resolve to a group outside it. The join still excludes
# rows with group_id IS NULL.
_SQL_GROUP_DEFAULT = (
    "SELECT c.group_id, COUNT(*) AS cnt FROM expenses e"  # noqa: S608
    " JOIN categories c ON c.id = e.category_id"
    " JOIN category_groups g ON g.id = c.group_id"
    f" WHERE {_MANUAL_RECENCY}"
    " GROUP BY c.group_id ORDER BY cnt DESC LIMIT 1"
)


def frequent_categories_sync(con: sqlite3.Connection, limit: int = 5) -> list[FrequentCategory]:
    rows = con.execute(
        f"""
        SELECT e.category_id, c.name, COUNT(*) AS cnt
          FROM expenses e JOIN categories c ON c.id = e.category_id
         WHERE {VISIBLE_CATEGORY_PREDICATE}
           AND e.receipt_id IS NULL
           AND e.datetime >= datetime('now', '-3 months')
         GROUP BY e.category_id ORDER BY cnt DESC LIMIT ?
        """,  # noqa: S608
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
        "SELECT id, code, name, sort_order, is_active FROM category_groups ORDER BY sort_order, id",
    ).fetchall()
    category_rows = con.execute(
        "SELECT c.id, c.code, c.name, c.group_id, g.name, c.is_active, c.is_hidden, c.is_retired"
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
                "code": str(r[1]),
                "name": str(r[2]),
                "sort_order": int(r[3]),
                "is_active": bool(r[4]),
                "removable": group_children.get(int(r[0]), 0) == 0,
            }
            for r in group_rows
        ],
        "categories": [
            {
                "id": int(r[0]),
                "code": str(r[1]),
                "name": str(r[2]),
                "group_id": int(r[3]),
                "group": str(r[4]),
                "is_active": bool(r[5]),
                "is_hidden": bool(r[6]),
                "is_retired": bool(r[7]),
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
