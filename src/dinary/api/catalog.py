"""Catalog API: /api/catalog + /api/catalog/*"""

import logging
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from dinary.api.controllers.catalog import (
    AdminMutationResponse,
    CatalogResponse,
    EventAddBody,
    EventAddResponse,
    EventPatchBody,
    GroupAddBody,
    GroupAddResponse,
    GroupPatchBody,
    ReloadMapResponse,
    TagAddBody,
    TagAddResponse,
    TagPatchBody,
    _event_item,
    _group_item,
    _tag_item,
    build_catalog_snapshot,
    etag_for,
    handle_catalog_error,
    if_none_match_matches,
)
from dinary.api.controllers.catalog_writer_events import (
    add_event,
    add_tag,
    delete_event,
    delete_tag,
    edit_event,
    edit_tag,
)
from dinary.api.controllers.catalog_writer_groups import add_group, delete_group, edit_group
from dinary.config import settings, spreadsheet_id_from_setting
from dinary.db import storage
from dinary.db.catalog import get_catalog_version
from dinary.db.storage import get_db
from dinary.sheets import sheet_mapping

logger = logging.getLogger(__name__)
router = APIRouter()


def _etag_response(
    con: sqlite3.Connection,
    response: Response,
    delete_result=None,
) -> AdminMutationResponse:
    version = get_catalog_version(con)
    body = AdminMutationResponse(
        catalog_version=version,
        delete_status=delete_result.status if delete_result else None,
        usage_count=delete_result.usage_count if delete_result else None,
    )
    response.headers["ETag"] = etag_for(version)
    return body


@router.get("/api/catalog", response_model=None)
def get_catalog(
    response: Response,
    if_none_match: str | None = Header(default=None),
) -> CatalogResponse | Response:
    try:
        with storage.connection() as con:
            snapshot = build_catalog_snapshot(con)
    except Exception:
        logger.exception("Failed to load catalog snapshot")
        raise HTTPException(status_code=500, detail="Failed to load catalog") from None

    etag = etag_for(snapshot["catalog_version"])
    if if_none_match is not None and if_none_match_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return CatalogResponse(**snapshot)


@router.post("/api/catalog/groups", response_model=GroupAddResponse)
def add_group_endpoint(
    body: GroupAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> GroupAddResponse:
    with handle_catalog_error():
        result = add_group(con, name=body.name, sort_order=body.sort_order)
    version = get_catalog_version(con)
    response.headers["ETag"] = etag_for(version)
    return GroupAddResponse(
        catalog_version=version,
        status=result.status,
        group=_group_item(con, result.id),
    )


@router.patch("/api/catalog/groups/{group_id}", response_model=AdminMutationResponse)
def edit_group_endpoint(
    group_id: int,
    body: GroupPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminMutationResponse:
    with handle_catalog_error():
        edit_group(
            con,
            group_id,
            name=body.name,
            sort_order=body.sort_order,
            is_active=body.is_active,
        )
    return _etag_response(con, response)


@router.delete("/api/catalog/groups/{group_id}", response_model=AdminMutationResponse)
def delete_group_endpoint(
    group_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminMutationResponse:
    with handle_catalog_error():
        result = delete_group(con, group_id)
    return _etag_response(con, response, delete_result=result)


@router.post("/api/catalog/events", response_model=EventAddResponse)
def add_event_endpoint(
    body: EventAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> EventAddResponse:
    with handle_catalog_error():
        result = add_event(
            con,
            name=body.name,
            date_from=body.date_from,
            date_to=body.date_to,
            auto_attach_enabled=body.auto_attach_enabled,
            auto_tags=body.auto_tags,
        )
    version = get_catalog_version(con)
    response.headers["ETag"] = etag_for(version)
    return EventAddResponse(
        catalog_version=version,
        status=result.status,
        event=_event_item(con, result.id),
    )


@router.patch("/api/catalog/events/{event_id}", response_model=AdminMutationResponse)
def edit_event_endpoint(
    event_id: int,
    body: EventPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminMutationResponse:
    with handle_catalog_error():
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
    return _etag_response(con, response)


@router.delete("/api/catalog/events/{event_id}", response_model=AdminMutationResponse)
def delete_event_endpoint(
    event_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminMutationResponse:
    with handle_catalog_error():
        result = delete_event(con, event_id)
    return _etag_response(con, response, delete_result=result)


@router.post("/api/catalog/tags", response_model=TagAddResponse)
def add_tag_endpoint(
    body: TagAddBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> TagAddResponse:
    with handle_catalog_error():
        result = add_tag(con, name=body.name)
    version = get_catalog_version(con)
    response.headers["ETag"] = etag_for(version)
    return TagAddResponse(
        catalog_version=version,
        status=result.status,
        tag=_tag_item(con, result.id),
    )


@router.patch("/api/catalog/tags/{tag_id}", response_model=AdminMutationResponse)
def edit_tag_endpoint(
    tag_id: int,
    body: TagPatchBody,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminMutationResponse:
    with handle_catalog_error():
        edit_tag(con, tag_id, name=body.name, is_active=body.is_active)
    return _etag_response(con, response)


@router.delete("/api/catalog/tags/{tag_id}", response_model=AdminMutationResponse)
def delete_tag_endpoint(
    tag_id: int,
    response: Response,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> AdminMutationResponse:
    with handle_catalog_error():
        result = delete_tag(con, tag_id)
    return _etag_response(con, response, delete_result=result)


@router.post("/api/catalog/reload-map", response_model=ReloadMapResponse)
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
