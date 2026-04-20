"""GET /api/categories endpoint."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dinary.services import duckdb_repo

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryItem(BaseModel):
    name: str
    group: str


class CategoriesResponse(BaseModel):
    catalog_version: int
    categories: list[CategoryItem]


@router.get("/api/categories", response_model=CategoriesResponse)
def list_categories() -> CategoriesResponse:
    try:
        con = duckdb_repo.get_connection()
        try:
            rows = duckdb_repo.list_categories(con)
            version = duckdb_repo.get_catalog_version(con)
        finally:
            con.close()

        items = [CategoryItem(name=r.name, group=r.group_name) for r in rows]
        return CategoriesResponse(catalog_version=version, categories=items)
    except Exception:
        logger.exception("Failed to load categories")
        raise HTTPException(status_code=500, detail="Failed to load categories") from None
