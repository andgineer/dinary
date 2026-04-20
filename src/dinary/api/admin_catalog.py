"""Admin HTTP surface: catalog CRUD + runtime-map reload.

All endpoints are token-protected via ``DINARY_ADMIN_API_TOKEN``. An
empty token means the admin API is disabled entirely: every endpoint
responds with ``503`` so a production deployment without the token
can't be mutated through the PWA.

Write helpers live in ``catalog_writer.py``; this module is a thin
HTTP veneer that:

* Validates request bodies.
* Opens a DuckDB cursor.
* Delegates to ``catalog_writer`` in a single call per request (PATCH
  is atomic: if a body carries both ``name`` and ``is_active``, the
  catalog_writer runs them in one transaction).
* Returns the *full* catalog snapshot + fresh ETag so the PWA can
  swap its cached catalog in one round-trip, without a follow-up
  ``GET /api/catalog``.
"""

import hmac
import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, Field

from dinary.config import settings
from dinary.services import catalog_writer, duckdb_repo, runtime_map

from .catalog import (
    CategoryGroupItem,
    CategoryItem,
    EventItem,
    TagItem,
    _etag_for,
    build_catalog_snapshot,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _require_admin_token(authorization: str | None) -> None:
    """Accept ``Authorization: Bearer <token>`` matching ``settings.admin_api_token``.

    Disabled (token empty) => 503 for every endpoint.
    Missing / malformed header => 401.
    Wrong token => 403.
    """
    expected = settings.admin_api_token
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="admin API disabled (DINARY_ADMIN_API_TOKEN empty)",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="missing Bearer token",
        )
    token = authorization[len("Bearer ") :].strip()
    # Constant-time compare so an attacker cannot brute-force the token
    # byte-by-byte via response-time differences. ``compare_digest``
    # handles unequal-length inputs internally (it still runs in
    # constant time, just returns ``False`` fast).
    if not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="invalid admin token")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class EventAddBody(BaseModel):
    name: str = Field(min_length=1)
    date_from: date
    date_to: date
    auto_attach_enabled: bool = False


class EventPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    date_from: date | None = None
    date_to: date | None = None
    auto_attach_enabled: bool | None = None
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


AddStatusLiteral = Literal["created", "reactivated", "noop"]


class AdminCatalogResponse(BaseModel):
    """Successful mutation response: new id + status + full catalog snapshot.

    The snapshot lets the PWA replace its ``localStorage["catalog"]``
    in a single round-trip instead of calling ``GET /api/catalog``.
    Inner item types are reused from ``catalog.py`` so the admin
    response and ``GET /api/catalog`` response are structurally
    identical (PWA treats them interchangeably).

    ``status`` is only meaningful on POST (add) routes:

    * ``"created"`` — a brand-new row was inserted.
    * ``"reactivated"`` — an inactive row with the same name was
      flipped back to ``is_active=TRUE`` (existing fields preserved;
      use PATCH to change them).
    * ``"noop"`` — a row already existed and was already active;
      nothing changed and ``catalog_version`` was not bumped.

    PATCH routes leave ``status`` unset.
    """

    new_id: int | None = None
    status: AddStatusLiteral | None = None
    catalog_version: int
    category_groups: list[CategoryGroupItem]
    categories: list[CategoryItem]
    events: list[EventItem]
    tags: list[TagItem]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _wrap_catalog_error(exc: catalog_writer.CatalogWriteError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _snapshot_response(
    con,
    response: Response,
    new_id: int | None = None,
    status: AddStatusLiteral | None = None,
) -> AdminCatalogResponse:
    """Build the full catalog snapshot response every admin write returns.

    Takes the caller's already-open DuckDB connection so a single
    request uses exactly one DB connection (write + snapshot). Opening
    a second connection per request added latency without any
    isolation benefit — the catalog_writer commits before we build
    the snapshot, so reusing the connection sees the freshly-committed
    rows and cannot read a stale view.

    Also stamps the ``ETag`` response header so PWA clients can
    keep the admin-response path and the ``GET /api/catalog`` path
    on the same cache key. The PWA derives the ETag locally from
    ``catalog_version`` anyway, but emitting it here keeps raw HTTP
    tooling (curl, proxies) in sync.
    """
    snapshot = build_catalog_snapshot(con)
    response.headers["ETag"] = _etag_for(snapshot["catalog_version"])
    return AdminCatalogResponse(new_id=new_id, status=status, **snapshot)


# ---------------------------------------------------------------------------
# Category groups
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/groups", response_model=AdminCatalogResponse)
def add_group(
    body: GroupAddBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            result = catalog_writer.add_group(
                con,
                name=body.name,
                sort_order=body.sort_order,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response, new_id=result.id, status=result.status)
    finally:
        con.close()


@router.patch("/api/admin/catalog/groups/{group_id}", response_model=AdminCatalogResponse)
def edit_group(
    group_id: int,
    body: GroupPatchBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            catalog_writer.edit_group(
                con,
                group_id,
                name=body.name,
                sort_order=body.sort_order,
                is_active=body.is_active,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/categories", response_model=AdminCatalogResponse)
def add_category(
    body: CategoryAddBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            result = catalog_writer.add_category(
                con,
                name=body.name,
                group_id=body.group_id,
                sheet_name=body.sheet_name,
                sheet_group=body.sheet_group,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response, new_id=result.id, status=result.status)
    finally:
        con.close()


@router.patch("/api/admin/catalog/categories/{category_id}", response_model=AdminCatalogResponse)
def edit_category(
    category_id: int,
    body: CategoryPatchBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            catalog_writer.edit_category(
                con,
                category_id,
                name=body.name,
                group_id=body.group_id,
                sheet_name=body.sheet_name,
                sheet_group=body.sheet_group,
                is_active=body.is_active,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/events", response_model=AdminCatalogResponse)
def add_event(
    body: EventAddBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            result = catalog_writer.add_event(
                con,
                name=body.name,
                date_from=body.date_from,
                date_to=body.date_to,
                auto_attach_enabled=body.auto_attach_enabled,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response, new_id=result.id, status=result.status)
    finally:
        con.close()


@router.patch("/api/admin/catalog/events/{event_id}", response_model=AdminCatalogResponse)
def edit_event(
    event_id: int,
    body: EventPatchBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            catalog_writer.edit_event(
                con,
                event_id,
                name=body.name,
                date_from=body.date_from,
                date_to=body.date_to,
                auto_attach_enabled=body.auto_attach_enabled,
                is_active=body.is_active,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/tags", response_model=AdminCatalogResponse)
def add_tag(
    body: TagAddBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            result = catalog_writer.add_tag(con, name=body.name)
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response, new_id=result.id, status=result.status)
    finally:
        con.close()


@router.patch("/api/admin/catalog/tags/{tag_id}", response_model=AdminCatalogResponse)
def edit_tag(
    tag_id: int,
    body: TagPatchBody,
    response: Response,
    authorization: str | None = Header(default=None),
) -> AdminCatalogResponse:
    _require_admin_token(authorization)
    con = duckdb_repo.get_connection()
    try:
        try:
            catalog_writer.edit_tag(
                con,
                tag_id,
                name=body.name,
                is_active=body.is_active,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Runtime map reload
# ---------------------------------------------------------------------------


class ReloadMapResponse(BaseModel):
    """Runtime-map reload result.

    Success is encoded by HTTP 200 alone — any failure raises
    ``HTTPException`` before we reach this model, so a dedicated
    ``status: Literal["ok"]`` field would only ever carry that one
    value and was removed as noise.

    ``modified_time_cached`` is always ``True`` on the admin path
    (we skip the lost-update second GET there) but the field is
    kept in the response shape so the drain-loop path — which may
    eventually also be exposed — can share the model.
    """

    row_count: int
    modified_time: str
    tab: str
    modified_time_cached: bool


@router.post("/api/admin/reload-map", response_model=ReloadMapResponse)
def reload_map(
    authorization: str | None = Header(default=None),
) -> ReloadMapResponse:
    _require_admin_token(authorization)
    if not settings.sheet_logging_spreadsheet:
        # Consistent with the rest of the admin surface: missing
        # server-side configuration is "service not available", not
        # "bad request".
        raise HTTPException(
            status_code=503,
            detail="sheet_logging_spreadsheet not configured; reload-map unavailable",
        )
    try:
        # ``check_after=False`` halves the Drive metadata GETs on this
        # hot admin button (see ``reload_now`` docstring for the
        # correctness argument).
        summary = runtime_map.reload_now(check_after=False)
    except runtime_map.MapTabError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return ReloadMapResponse(**summary)
