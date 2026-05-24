"""Expense corrections API: PATCH /api/expenses/{id}/category"""

import sqlite3

from fastapi import APIRouter, Depends

from dinary.api.controllers.expense_corrections import (
    CategoryCorrectionRequest,
    CategoryCorrectionResponse,
    correct_category_sync,
)
from dinary.db.storage import get_db

router = APIRouter()


@router.patch("/api/expenses/{expense_id}/category", response_model=CategoryCorrectionResponse)
def correct_expense_category(
    expense_id: int,
    req: CategoryCorrectionRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryCorrectionResponse:
    return correct_category_sync(expense_id, req, con)
