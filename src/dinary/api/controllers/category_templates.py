"""Category templates business logic: Pydantic models + thin controller helpers."""

import json
import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.api.controllers.catalog import (
    CatalogResponse,
    CatalogVersionResponse,
    CategoryResultResponse,
    _category_item,
    build_catalog_snapshot,
)
from dinary.db.catalog import (
    activate_category,
    create_category,
    get_active_template,
    get_catalog_version,
    hide_category,
    move_category,
    rename_category,
    unhide_category,
)
from dinary.db.category_apply import (
    DEFAULT_LANG,
    apply_template,
    load_category_translations,
    resolve_category_name,
)

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class TemplatePreviewCategory(BaseModel):
    names: dict[str, str]


class TemplatePreviewGroup(BaseModel):
    code: str
    names: dict[str, str]
    categories: list[TemplatePreviewCategory]


class CategoryTemplateItem(BaseModel):
    code: str
    names: dict[str, str]
    taglines: dict[str, str]
    origin: str
    groups: list[TemplatePreviewGroup]


class ActiveTemplateResponse(BaseModel):
    active_template: str | None


class ApplyTemplateBody(BaseModel):
    code: str
    lang: str


class CategoryMutationResponse(CatalogResponse):
    active_template: str


class CreateCategoryBody(BaseModel):
    name: str = Field(min_length=1)
    group_code: str


class MoveCategoryBody(BaseModel):
    group_code: str


class RenameCategoryBody(BaseModel):
    name: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Controller helpers
# ---------------------------------------------------------------------------


def list_category_templates(con: sqlite3.Connection) -> list[CategoryTemplateItem]:
    rows = con.execute(
        "SELECT code, origin, definition_json FROM category_templates ORDER BY sort_order",
    ).fetchall()
    translations = load_category_translations(con)
    items = []
    for code, origin, definition_json in rows:
        definition = json.loads(definition_json)
        items.append(
            CategoryTemplateItem(
                code=str(code),
                names=definition["names"],
                taglines=definition["taglines"],
                origin=str(origin),
                groups=_build_template_groups(definition, translations),
            ),
        )
    return items


def _build_template_groups(
    definition: dict,
    translations: dict[str, dict[str, str]],
) -> list[TemplatePreviewGroup]:
    """Build the ordered, visible-only group/category preview for a template."""
    langs = list(definition["names"].keys())
    visible = definition["visible"]
    result = []
    for group_code, group_names in definition["groups"].items():
        codes = visible.get(group_code, [])
        if not codes:
            continue
        result.append(
            TemplatePreviewGroup(
                code=group_code,
                names={
                    lang: group_names.get(lang, group_names.get(DEFAULT_LANG, group_code))
                    for lang in langs
                },
                categories=[
                    TemplatePreviewCategory(
                        names={
                            lang: resolve_category_name(translations, definition, code, lang)
                            for lang in langs
                        },
                    )
                    for code in codes
                ],
            ),
        )
    return result


def get_active_template_response(con: sqlite3.Connection) -> ActiveTemplateResponse:
    return ActiveTemplateResponse(active_template=get_active_template(con))


def apply_template_sync(
    con: sqlite3.Connection,
    body: ApplyTemplateBody,
) -> CategoryMutationResponse:
    try:
        apply_template(con, body.code, body.lang)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CategoryMutationResponse(**build_catalog_snapshot(con), active_template=body.code)


def create_category_sync(
    con: sqlite3.Connection,
    body: CreateCategoryBody,
) -> CategoryResultResponse:
    try:
        code = create_category(con, body.name, body.group_code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CategoryResultResponse(
        catalog_version=get_catalog_version(con),
        category=_category_item(con, code),
    )


def activate_category_sync(con: sqlite3.Connection, code: str) -> CategoryResultResponse:
    try:
        activate_category(con, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return CategoryResultResponse(
        catalog_version=get_catalog_version(con),
        category=_category_item(con, code),
    )


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
