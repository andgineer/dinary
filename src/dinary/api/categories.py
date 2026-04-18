"""GET /api/categories endpoint."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dinary.services import duckdb_repo

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryItem(BaseModel):
    """One row of the 3D classification catalog."""

    name: str
    group: str


class CategoriesResponse(BaseModel):
    """Top-level response: catalog + monotonically increasing version.

    `catalog_version` is the number bumped by `inv rebuild-catalog`. Clients
    cache it together with `categories` and refresh both atomically when the
    server reports a higher value (echoed by `POST /api/expenses` too).
    """

    catalog_version: int
    categories: list[CategoryItem]


@router.get("/api/categories", response_model=CategoriesResponse)
def list_categories() -> CategoriesResponse:
    try:
        con = duckdb_repo.get_config_connection(read_only=True)
        try:
            rows = duckdb_repo.list_categories(con)
            version = duckdb_repo.get_catalog_version(con)
        finally:
            con.close()

        items = [CategoryItem(name=r.name, group=r.group_name) for r in rows]
        return CategoriesResponse(catalog_version=version, categories=items)
    except Exception:
        # 500 Internal Server Error, not 502. We aren't proxying to a
        # downstream service here — this endpoint reads `config.duckdb`
        # directly, so any failure is on us. 502 would imply Google Sheets
        # or some other upstream is unreachable, which would mislead the
        # client into retrying instead of paging the operator.
        logger.exception("Failed to load categories")
        raise HTTPException(status_code=500, detail="Failed to load categories") from None
