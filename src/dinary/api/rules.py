"""Rules API: /api/rules/*"""

import sqlite3

from fastapi import APIRouter, Depends, Query

from dinary.api.controllers.rules import build_rules_counts, build_rules_feed
from dinary.db.storage import get_db

router = APIRouter()


@router.get("/api/rules/feed")
def rules_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return build_rules_feed(con, page, page_size)


@router.get("/api/rules/counts")
def rules_counts(con: sqlite3.Connection = Depends(get_db)) -> dict:  # noqa: B008
    return build_rules_counts(con)
