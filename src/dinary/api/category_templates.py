"""Category templates API: /api/category-templates + /api/categories"""

import sqlite3

from fastapi import APIRouter, Depends, Header, Response

from dinary.api.controllers.catalog import etag_for, if_none_match_matches
from dinary.api.controllers.category_templates import (
    ActiveTemplateResponse,
    ApplyTemplateBody,
    ApplyTemplateResponse,
    CatalogVersionResponse,
    CategoriesResponse,
    CategorySearchItem,
    CategoryTemplateItem,
    CreateCategoryBody,
    CreateCategoryResponse,
    MoveCategoryBody,
    RenameCategoryBody,
    activate_category_sync,
    apply_template_sync,
    create_category_sync,
    get_active_template_response,
    get_categories_response,
    hide_category_sync,
    list_category_templates,
    move_category_sync,
    rename_category_sync,
    search_categories_response,
    unhide_category_sync,
)
from dinary.db.storage import get_db

router = APIRouter()


@router.get("/api/category-templates", response_model=list[CategoryTemplateItem])
def get_category_templates(
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> list[CategoryTemplateItem]:
    return list_category_templates(con)


@router.get("/api/category-templates/active", response_model=ActiveTemplateResponse)
def get_active_template_endpoint(
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ActiveTemplateResponse:
    return get_active_template_response(con)


@router.post("/api/category-templates/apply", response_model=ApplyTemplateResponse)
def apply_category_template(
    body: ApplyTemplateBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ApplyTemplateResponse:
    return apply_template_sync(con, body)


@router.get("/api/categories", response_model=None)
def get_categories(
    response: Response,
    if_none_match: str | None = Header(default=None),
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoriesResponse | Response:
    payload = get_categories_response(con)
    etag = etag_for(payload.catalog_version)
    if if_none_match is not None and if_none_match_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})

    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return payload


@router.get("/api/categories/search", response_model=list[CategorySearchItem])
def search_categories_endpoint(
    q: str = "",
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> list[CategorySearchItem]:
    return search_categories_response(con, q)


@router.post("/api/categories", response_model=CreateCategoryResponse, status_code=201)
def create_category_endpoint(
    body: CreateCategoryBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CreateCategoryResponse:
    return create_category_sync(con, body)


@router.post("/api/categories/{code}/activate", response_model=CatalogVersionResponse)
def activate_category_endpoint(
    code: str,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CatalogVersionResponse:
    return activate_category_sync(con, code)


@router.post("/api/categories/{code}/hide", response_model=CatalogVersionResponse)
def hide_category_endpoint(
    code: str,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CatalogVersionResponse:
    return hide_category_sync(con, code)


@router.post("/api/categories/{code}/unhide", response_model=CatalogVersionResponse)
def unhide_category_endpoint(
    code: str,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CatalogVersionResponse:
    return unhide_category_sync(con, code)


@router.post("/api/categories/{code}/move", response_model=CatalogVersionResponse)
def move_category_endpoint(
    code: str,
    body: MoveCategoryBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CatalogVersionResponse:
    return move_category_sync(con, code, body)


@router.post("/api/categories/{code}/rename", response_model=CatalogVersionResponse)
def rename_category_endpoint(
    code: str,
    body: RenameCategoryBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CatalogVersionResponse:
    return rename_category_sync(con, code, body)
