"""Admin HTTP veneer: catalog CRUD + sheet-mapping reload.

No authentication yet — deployments must sit behind a private network or ACL.
Each write delegates to catalog_writer_* in one transaction and returns the
full catalog snapshot so the PWA can refresh in one round-trip.
See ``specs/reference/catalog-api.md``.
"""

import logging
import sqlite3
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from dinary.config import settings, spreadsheet_id_from_setting
from dinary.services import sheet_mapping
from dinary.services.catalog_writer_categories import (
    add_category,
    delete_category,
    edit_category,
)
from dinary.services.catalog_writer_errors import AddResult, CatalogWriteError, DeleteResult
from dinary.services.catalog_writer_events import (
    add_event,
    add_tag,
    delete_event,
    delete_tag,
    edit_event,
    edit_tag,
)
from dinary.services.catalog_writer_groups import add_group, delete_group, edit_group
from dinary.services.storage import get_db

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
# Request bodies
# ---------------------------------------------------------------------------


class EventAddBody(BaseModel):
    name: str = Field(min_length=1)
    date_from: date
    date_to: date
    auto_attach_enabled: bool = False
    auto_tags: list[str] | None = None


class EventPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    date_from: date | None = None
    date_to: date | None = None
    auto_attach_enabled: bool | None = None
    auto_tags: list[str] | None = None
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
DeleteStatusLiteral = Literal["hard", "soft"]


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

    PATCH routes leave ``status`` unset. DELETE routes leave
    ``status`` unset but set ``delete_status`` + ``usage_count``.
    """

    new_id: int | None = None
    status: AddStatusLiteral | None = None
    delete_status: DeleteStatusLiteral | None = None
    usage_count: int | None = None
    catalog_version: int
    category_groups: list[CategoryGroupItem]
    categories: list[CategoryItem]
    events: list[EventItem]
    tags: list[TagItem]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _wrap_catalog_error(exc: CatalogWriteError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _snapshot_response(
    con,
    response: Response,
    add_result: AddResult | None = None,
    delete_result: DeleteResult | None = None,
) -> AdminCatalogResponse:
    """Build the full catalog snapshot response every admin write returns.

    Takes the caller's already-open SQLite connection so a single
    request uses exactly one DB connection (write + snapshot).
    """
    snapshot = build_catalog_snapshot(con)
    response.headers["ETag"] = _etag_for(snapshot["catalog_version"])
    return AdminCatalogResponse(
        new_id=add_result.id if add_result else None,
        status=add_result.status if add_result else None,
        delete_status=delete_result.status if delete_result else None,
        usage_count=delete_result.usage_count if delete_result else None,
        **snapshot,
    )


# ---------------------------------------------------------------------------
# Category groups
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/groups", response_model=AdminCatalogResponse)
def add_group_endpoint(
    body: GroupAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = add_group(con, name=body.name, sort_order=body.sort_order)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, add_result=result)


@router.patch("/api/admin/catalog/groups/{group_id}", response_model=AdminCatalogResponse)
def edit_group_endpoint(
    group_id: int,
    body: GroupPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        edit_group(
            con,
            group_id,
            name=body.name,
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response)


@router.delete("/api/admin/catalog/groups/{group_id}", response_model=AdminCatalogResponse)
def delete_group_endpoint(
    group_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = delete_group(con, group_id)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, delete_result=result)


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/categories", response_model=AdminCatalogResponse)
def add_category_endpoint(
    body: CategoryAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = add_category(
            con,
            name=body.name,
            group_id=body.group_id,
            sheet_name=body.sheet_name,
            sheet_group=body.sheet_group,
        )
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, add_result=result)


@router.patch("/api/admin/catalog/categories/{category_id}", response_model=AdminCatalogResponse)
def edit_category_endpoint(
    category_id: int,
    body: CategoryPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        edit_category(
            con,
            category_id,
            name=body.name,
            group_id=body.group_id,
            sheet_name=body.sheet_name,
            sheet_group=body.sheet_group,
            is_active=body.is_active,
        )
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response)


@router.delete(
    "/api/admin/catalog/categories/{category_id}",
    response_model=AdminCatalogResponse,
)
def delete_category_endpoint(
    category_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = delete_category(con, category_id)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, delete_result=result)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/events", response_model=AdminCatalogResponse)
def add_event_endpoint(
    body: EventAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = add_event(
            con,
            name=body.name,
            date_from=body.date_from,
            date_to=body.date_to,
            auto_attach_enabled=body.auto_attach_enabled,
            auto_tags=body.auto_tags,
        )
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, add_result=result)


@router.patch("/api/admin/catalog/events/{event_id}", response_model=AdminCatalogResponse)
def edit_event_endpoint(
    event_id: int,
    body: EventPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        edit_event(
            con,
            event_id,
            name=body.name,
            date_from=body.date_from,
            date_to=body.date_to,
            auto_attach_enabled=body.auto_attach_enabled,
            auto_tags=body.auto_tags,
            is_active=body.is_active,
        )
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response)


@router.delete("/api/admin/catalog/events/{event_id}", response_model=AdminCatalogResponse)
def delete_event_endpoint(
    event_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = delete_event(con, event_id)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, delete_result=result)


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/tags", response_model=AdminCatalogResponse)
def add_tag_endpoint(
    body: TagAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = add_tag(con, name=body.name)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, add_result=result)


@router.patch("/api/admin/catalog/tags/{tag_id}", response_model=AdminCatalogResponse)
def edit_tag_endpoint(
    tag_id: int,
    body: TagPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        edit_tag(con, tag_id, name=body.name, is_active=body.is_active)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response)


@router.delete("/api/admin/catalog/tags/{tag_id}", response_model=AdminCatalogResponse)
def delete_tag_endpoint(
    tag_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminCatalogResponse:
    try:
        result = delete_tag(con, tag_id)
    except CatalogWriteError as exc:
        raise _wrap_catalog_error(exc) from None
    return _snapshot_response(con, response, delete_result=result)


# ---------------------------------------------------------------------------
# Sheet-mapping reload
# ---------------------------------------------------------------------------


class ReloadMapResponse(BaseModel):
    """Sheet-mapping reload result.

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
def reload_map() -> ReloadMapResponse:
    if spreadsheet_id_from_setting(settings.sheet_logging_spreadsheet) is None:
        raise HTTPException(
            status_code=503,
            detail="sheet_logging_spreadsheet not configured; reload-map unavailable",
        )
    try:
        summary = sheet_mapping.reload_now(check_after=False)
    except sheet_mapping.MapTabError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return ReloadMapResponse(**summary)
