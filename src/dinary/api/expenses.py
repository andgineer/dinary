"""Expenses API: POST /api/expenses"""

import asyncio
import sqlite3

from fastapi import APIRouter, Depends

from dinary.api.controllers.expenses import ExpenseRequest, ExpenseResponse, create_expense_sync
from dinary.background.sheet_logging.sheet_logging import notify_new_work
from dinary.config import settings
from dinary.db.storage import get_db

router = APIRouter()


@router.post("/api/expenses", response_model=ExpenseResponse)
async def create_expense(
    req: ExpenseRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ExpenseResponse:
    resp = await asyncio.to_thread(create_expense_sync, req, con)
    if resp.status == "ok" and settings.sheet_logging_enabled:
        notify_new_work()
    return resp
