"""GET /api/catalog — one-shot, ETag-cacheable catalog snapshot.

Single endpoint supplying the PWA's full 3D taxonomy (groups,
categories, events, tags) on the hot path. Payload:

    {
      catalog_version: int,
      categories:       [{id, name, group, group_id}],
      category_groups:  [{id, name, sort_order}],
      events:           [{id, name, date_from, date_to,
                          auto_attach_enabled}],
      tags:             [{id, name}],
    }

ETag is ``W/"catalog-v<N>"`` where N is ``catalog_version``. It rides
on the HTTP ``ETag`` response header only — the body does **not**
carry a duplicate ``etag`` field because the value is a pure function
of ``catalog_version`` and the PWA can derive it client-side. Clients
send ``If-None-Match`` on subsequent polls; a match returns ``304 Not
Modified`` with no body. Coupled with the PWA's catalog cache + the
``catalog_version`` bump emitted from every ``POST /api/expenses``
response, the steady state is **zero catalog GETs per expense**.

All events are returned unfiltered; the PWA applies its own "active
within ±30 days" filter client-side so the server cache doesn't churn
every time the window rolls forward by a day.
"""

import logging

import duckdb
from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel

from dinary.services import duckdb_repo

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryGroupItem(BaseModel):
    id: int
    name: str
    sort_order: int


class CategoryItem(BaseModel):
    id: int
    name: str
    group: str
    group_id: int


class EventItem(BaseModel):
    id: int
    name: str
    date_from: str
    date_to: str
    auto_attach_enabled: bool


class TagItem(BaseModel):
    id: int
    name: str


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
    """RFC 7232-compliant ``If-None-Match`` match against a single ETag.

    ``If-None-Match`` is a comma-separated list of entity tags (and
    optionally the special ``*`` wildcard that matches any existing
    representation). Browsers in practice only ever send the single
    cached tag back, but proxies and curl calls can send a list —
    returning 304 on *any* list member is the correct behaviour so we
    don't re-download the catalog for those callers.
    """
    stripped = header_value.strip()
    if not stripped:
        return False
    if stripped == "*":
        # "*" always matches a representation that exists (it does).
        return True
    return any(token.strip() == etag for token in stripped.split(","))


def build_catalog_snapshot(con: duckdb.DuckDBPyConnection) -> dict:
    """Shared by GET /api/catalog and the admin POST/PATCH responses.

    Returns a dict-of-lists suitable for embedding directly in a
    pydantic response model (``CatalogResponse`` / ``AdminCatalogResponse``).
    """
    version = duckdb_repo.get_catalog_version(con)

    group_rows = con.execute(
        "SELECT id, name, sort_order FROM category_groups WHERE is_active ORDER BY sort_order, id",
    ).fetchall()

    category_rows = con.execute(
        "SELECT c.id, c.name, c.group_id, g.name"
        " FROM categories c JOIN category_groups g ON g.id = c.group_id"
        " WHERE c.is_active AND g.is_active"
        " ORDER BY g.sort_order, c.name",
    ).fetchall()

    event_rows = con.execute(
        "SELECT id, name, date_from, date_to, auto_attach_enabled"
        " FROM events WHERE is_active ORDER BY date_from, name",
    ).fetchall()

    tag_rows = con.execute(
        "SELECT id, name FROM tags WHERE is_active ORDER BY id",
    ).fetchall()

    return {
        "catalog_version": version,
        "category_groups": [
            {"id": int(r[0]), "name": str(r[1]), "sort_order": int(r[2])} for r in group_rows
        ],
        "categories": [
            {
                "id": int(r[0]),
                "name": str(r[1]),
                "group_id": int(r[2]),
                "group": str(r[3]),
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
            }
            for r in event_rows
        ],
        "tags": [{"id": int(r[0]), "name": str(r[1])} for r in tag_rows],
    }


@router.get("/api/catalog", response_model=None)
def get_catalog(
    response: Response,
    if_none_match: str | None = Header(default=None),
) -> CatalogResponse | Response:
    try:
        con = duckdb_repo.get_connection()
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
