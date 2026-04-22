"""Admin HTTP surface: catalog CRUD + sheet-mapping reload.

Authentication is a **deliberate TODO**. The prior ``DINARY_ADMIN_API_TOKEN``
gate was removed because it was shared across every operator and bypassed
by the PWA on every request anyway (the UI stored it in localStorage and
replayed it verbatim). A proper authorization layer (OAuth / session /
per-user API key — to be decided) will land alongside multi-user support;
until then every admin endpoint is reachable by any caller that can reach
the server. Deployments must put the service behind a private network or
reverse-proxy ACL.

Write helpers live in ``catalog_writer.py``; this module is a thin
HTTP veneer that:

* Validates request bodies.
* Opens a SQLite connection.
* Delegates to ``catalog_writer`` in a single call per request (PATCH
  is atomic: if a body carries both ``name`` and ``is_active``, the
  catalog_writer runs them in one transaction).
* Returns the *full* catalog snapshot + fresh ETag so the PWA can
  swap its cached catalog in one round-trip, without a follow-up
  ``GET /api/catalog``.

DELETE endpoints apply soft/hard semantics: the row is physically
removed iff no ledger rows reference it; otherwise it is flipped to
``is_active=FALSE`` and the response carries ``delete_status="soft"``
plus ``usage_count`` so the PWA can tell the operator "still available
under Show inactive (N historical references)".
"""

import logging
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from dinary.config import settings, spreadsheet_id_from_setting
from dinary.services import catalog_writer, ledger_repo, sheet_mapping

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


def _wrap_catalog_error(exc: catalog_writer.CatalogWriteError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _snapshot_response(  # noqa: PLR0913
    con,
    response: Response,
    new_id: int | None = None,
    status: AddStatusLiteral | None = None,
    delete_status: DeleteStatusLiteral | None = None,
    usage_count: int | None = None,
) -> AdminCatalogResponse:
    """Build the full catalog snapshot response every admin write returns.

    Takes the caller's already-open SQLite connection so a single
    request uses exactly one DB connection (write + snapshot).
    """
    snapshot = build_catalog_snapshot(con)
    response.headers["ETag"] = _etag_for(snapshot["catalog_version"])
    return AdminCatalogResponse(
        new_id=new_id,
        status=status,
        delete_status=delete_status,
        usage_count=usage_count,
        **snapshot,
    )


# ---------------------------------------------------------------------------
# Category groups
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/groups", response_model=AdminCatalogResponse)
def add_group(
    body: GroupAddBody,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
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
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
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


@router.delete("/api/admin/catalog/groups/{group_id}", response_model=AdminCatalogResponse)
def delete_group(
    group_id: int,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
    try:
        try:
            result = catalog_writer.delete_group(con, group_id)
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(
            con,
            response,
            delete_status=result.status,
            usage_count=result.usage_count,
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/categories", response_model=AdminCatalogResponse)
def add_category(
    body: CategoryAddBody,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
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
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
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


@router.delete(
    "/api/admin/catalog/categories/{category_id}",
    response_model=AdminCatalogResponse,
)
def delete_category(
    category_id: int,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
    try:
        try:
            result = catalog_writer.delete_category(con, category_id)
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(
            con,
            response,
            delete_status=result.status,
            usage_count=result.usage_count,
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/events", response_model=AdminCatalogResponse)
def add_event(
    body: EventAddBody,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
    try:
        try:
            result = catalog_writer.add_event(
                con,
                name=body.name,
                date_from=body.date_from,
                date_to=body.date_to,
                auto_attach_enabled=body.auto_attach_enabled,
                auto_tags=body.auto_tags,
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
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
    try:
        try:
            catalog_writer.edit_event(
                con,
                event_id,
                name=body.name,
                date_from=body.date_from,
                date_to=body.date_to,
                auto_attach_enabled=body.auto_attach_enabled,
                auto_tags=body.auto_tags,
                is_active=body.is_active,
            )
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(con, response)
    finally:
        con.close()


@router.delete("/api/admin/catalog/events/{event_id}", response_model=AdminCatalogResponse)
def delete_event(
    event_id: int,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
    try:
        try:
            result = catalog_writer.delete_event(con, event_id)
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(
            con,
            response,
            delete_status=result.status,
            usage_count=result.usage_count,
        )
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------


@router.post("/api/admin/catalog/tags", response_model=AdminCatalogResponse)
def add_tag(
    body: TagAddBody,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
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
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
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


@router.delete("/api/admin/catalog/tags/{tag_id}", response_model=AdminCatalogResponse)
def delete_tag(
    tag_id: int,
    response: Response,
) -> AdminCatalogResponse:
    con = ledger_repo.get_connection()
    try:
        try:
            result = catalog_writer.delete_tag(con, tag_id)
        except catalog_writer.CatalogWriteError as exc:
            raise _wrap_catalog_error(exc) from None
        return _snapshot_response(
            con,
            response,
            delete_status=result.status,
            usage_count=result.usage_count,
        )
    finally:
        con.close()


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
    # Normalise first so a whitespace-only or malformed env value fails
    # the same 503 as an empty one, instead of being passed through to
    # ``reload_now`` and surfacing as a confusing Drive 404.
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
