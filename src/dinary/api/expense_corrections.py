"""Expense corrections API: PATCH /api/expenses/{id}/category"""

import sqlite3

from fastapi import APIRouter, Depends, Request

from dinary.api.controllers.expense_corrections import (
    CategoryCorrectionRequest,
    CategoryCorrectionResponse,
    correct_category_sync,
    record_correction_ratings,
)
from dinary.db.storage import get_db

router = APIRouter()


@router.patch("/api/expenses/{expense_id}/category", response_model=CategoryCorrectionResponse)
async def correct_expense_category(
    expense_id: int,
    req: CategoryCorrectionRequest,
    request: Request,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryCorrectionResponse:
    pending_ratings: list[tuple[str, float]] = []
    resp = correct_category_sync(expense_id, req, con, pending_ratings=pending_ratings)
    await record_correction_ratings(request.app.state.llms, pending_ratings)
    return resp
