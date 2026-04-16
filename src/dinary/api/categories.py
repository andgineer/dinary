"""GET /api/categories endpoint."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dinary.services import duckdb_repo
from dinary.services.duckdb_repo import SheetCategoryRow
from dinary.services.sql_loader import fetchall_as, load_sql

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryItem(BaseModel):
    name: str
    group: str


@router.get("/api/categories", response_model=list[CategoryItem])
def list_categories() -> list[CategoryItem]:
    try:
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            rows = fetchall_as(SheetCategoryRow, con, load_sql("list_sheet_categories.sql"))
        finally:
            con.close()

        return [CategoryItem(name=r.sheet_category, group=r.sheet_group) for r in rows]
    except Exception:
        logger.exception("Failed to load categories")
        raise HTTPException(
            status_code=502,
            detail="Failed to load categories",
        ) from None
