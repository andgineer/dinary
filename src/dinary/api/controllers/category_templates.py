"""Category templates business logic: Pydantic models + thin controller helpers."""

import json
import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.db.catalog import (
    activate_category,
    create_category,
    get_active_template,
    get_catalog_version,
    hide_category,
    list_visible_categories,
    move_category,
    rename_category,
    search_categories,
    unhide_category,
)
from dinary.db.category_apply import apply_template

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CategoryTemplateItem(BaseModel):
    code: str
    names: dict[str, str]
    taglines: dict[str, str]
    origin: str


class ActiveTemplateResponse(BaseModel):
    active_template: str | None


class ApplyTemplateBody(BaseModel):
    code: str
    lang: str


class ApplyTemplateResponse(BaseModel):
    active_template: str
    catalog_version: int


class VisibleCategoryItem(BaseModel):
    id: int
    code: str
    name: str
    group_id: int
    group_name: str
    group_sort_order: int


class CategoriesResponse(BaseModel):
    catalog_version: int
    categories: list[VisibleCategoryItem]


class CategorySearchItem(BaseModel):
    id: int
    code: str
    name: str
    is_active: bool
    is_hidden: bool


class CreateCategoryBody(BaseModel):
    name: str = Field(min_length=1)
    group_code: str


class CreateCategoryResponse(BaseModel):
    code: str
    catalog_version: int


class MoveCategoryBody(BaseModel):
    group_code: str


class RenameCategoryBody(BaseModel):
    name: str = Field(min_length=1)


class CatalogVersionResponse(BaseModel):
    catalog_version: int


# ---------------------------------------------------------------------------
# Controller helpers
# ---------------------------------------------------------------------------


def list_category_templates(con: sqlite3.Connection) -> list[CategoryTemplateItem]:
    rows = con.execute(
        "SELECT code, origin, definition_json FROM category_templates ORDER BY sort_order",
    ).fetchall()
    items = []
    for code, origin, definition_json in rows:
        definition = json.loads(definition_json)
        items.append(
            CategoryTemplateItem(
                code=str(code),
                names=definition["names"],
                taglines=definition["taglines"],
                origin=str(origin),
            ),
        )
    return items


def get_active_template_response(con: sqlite3.Connection) -> ActiveTemplateResponse:
    return ActiveTemplateResponse(active_template=get_active_template(con))


def apply_template_sync(con: sqlite3.Connection, body: ApplyTemplateBody) -> ApplyTemplateResponse:
    try:
        apply_template(con, body.code, body.lang)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return ApplyTemplateResponse(
        active_template=body.code,
        catalog_version=get_catalog_version(con),
    )


def get_categories_response(con: sqlite3.Connection) -> CategoriesResponse:
    rows = list_visible_categories(con)
    return CategoriesResponse(
        catalog_version=get_catalog_version(con),
        categories=[
            VisibleCategoryItem(
                id=row.id,
                code=row.code,
                name=row.name,
                group_id=row.group_id,
                group_name=row.group_name,
                group_sort_order=row.group_sort_order,
            )
            for row in rows
        ],
    )


def search_categories_response(con: sqlite3.Connection, query: str) -> list[CategorySearchItem]:
    rows = search_categories(con, query)
    return [
        CategorySearchItem(
            id=row.id,
            code=row.code,
            name=row.name,
            is_active=row.is_active,
            is_hidden=row.is_hidden,
        )
        for row in rows
    ]


def create_category_sync(
    con: sqlite3.Connection,
    body: CreateCategoryBody,
) -> CreateCategoryResponse:
    try:
        code = create_category(con, body.name, body.group_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CreateCategoryResponse(code=code, catalog_version=get_catalog_version(con))


def activate_category_sync(con: sqlite3.Connection, code: str) -> CatalogVersionResponse:
    try:
        activate_category(con, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))


def hide_category_sync(con: sqlite3.Connection, code: str) -> CatalogVersionResponse:
    try:
        hide_category(con, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))


def unhide_category_sync(con: sqlite3.Connection, code: str) -> CatalogVersionResponse:
    try:
        unhide_category(con, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))


def move_category_sync(
    con: sqlite3.Connection,
    code: str,
    body: MoveCategoryBody,
) -> CatalogVersionResponse:
    try:
        move_category(con, code, body.group_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))


def rename_category_sync(
    con: sqlite3.Connection,
    code: str,
    body: RenameCategoryBody,
) -> CatalogVersionResponse:
    try:
        rename_category(con, code, body.name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CatalogVersionResponse(catalog_version=get_catalog_version(con))
