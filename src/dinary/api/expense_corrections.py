"""Expense corrections API: PATCH /api/expenses/{id}/category"""

import asyncio
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
async def correct_expense_category(
    expense_id: int,
    req: CategoryCorrectionRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CategoryCorrectionResponse:
    return await asyncio.to_thread(correct_category_sync, expense_id, req, con)
