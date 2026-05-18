"""Expenses API: POST /api/expenses, GET /api/expenses/recent, PATCH /api/expenses/{id}"""

import asyncio
import sqlite3

from fastapi import APIRouter, Depends

from dinary.api.controllers.expenses import (
    ExpenseEditRequest,
    ExpenseEditResponse,
    ExpenseRequest,
    ExpenseResponse,
    RecentExpenseItem,
    create_expense_sync,
    edit_expense_sync,
    list_recent_expenses_sync,
)
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


@router.get("/api/expenses/recent", response_model=list[RecentExpenseItem])
async def get_recent_expenses(
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> list[RecentExpenseItem]:
    return await asyncio.to_thread(list_recent_expenses_sync, con)


@router.patch("/api/expenses/{expense_id}", response_model=ExpenseEditResponse)
async def patch_expense(
    expense_id: int,
    req: ExpenseEditRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ExpenseEditResponse:
    return await asyncio.to_thread(edit_expense_sync, expense_id, req, con)
