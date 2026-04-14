"""GET /api/categories endpoint."""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dinary.services.sheets import get_categories

logger = logging.getLogger(__name__)
router = APIRouter()


class CategoryItem(BaseModel):
    name: str
    group: str


@router.get("/api/categories", response_model=list[CategoryItem])
def list_categories() -> list[CategoryItem]:
    try:
        cats = get_categories()
    except Exception:
        logger.exception("Failed to load categories")
        raise HTTPException(status_code=502, detail="Failed to load categories from Google Sheets")

    return [CategoryItem(name=c.name, group=c.group) for c in cats]
