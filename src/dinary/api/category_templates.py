"""Category templates API: /api/category-templates + /api/categories"""

import sqlite3

from fastapi import APIRouter, Depends

from dinary.api.controllers.catalog import CatalogVersionResponse, CategoryResultResponse
from dinary.api.controllers.category_templates import (
    ActiveTemplateResponse,
    ApplyTemplateBody,
    CategoryMutationResponse,
    CategoryTemplateItem,
    CreateCategoryBody,
    MoveCategoryBody,
    RenameCategoryBody,
    activate_category_sync,
    apply_template_sync,
    create_category_sync,
    get_active_template_response,
    hide_category_sync,
    list_category_templates,
    move_category_sync,
    rename_category_sync,
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


@router.post("/api/category-templates/apply", response_model=CategoryMutationResponse)
def apply_category_template(
    body: ApplyTemplateBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryMutationResponse:
    return apply_template_sync(con, body)


@router.post("/api/categories", response_model=CategoryResultResponse, status_code=201)
def create_category_endpoint(
    body: CreateCategoryBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryResultResponse:
    return create_category_sync(con, body)


@router.post("/api/categories/{code}/activate", response_model=CategoryResultResponse)
def activate_category_endpoint(
    code: str,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryResultResponse:
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
