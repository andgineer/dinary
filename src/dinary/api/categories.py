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


@router.get("/api/categories", response_model=list[CategoryItem])
def list_categories() -> list[CategoryItem]:
    try:
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            rows = duckdb_repo.list_sheet_categories(con)
        finally:
            duckdb_repo.close_connection(con)

        return [CategoryItem(name=r.sheet_category, group=r.sheet_group) for r in rows]
    except Exception:
        logger.exception("Failed to load categories")
        raise HTTPException(
            status_code=502,
            detail="Failed to load categories",
        ) from None
