"""Expenses API: POST /api/expenses, GET /api/expenses, PATCH /api/expenses/{id},
DELETE /api/expenses/{id}"""

import sqlite3

from fastapi import APIRouter, Depends, Query, Response

from dinary.api.controllers.expenses import (
    ExpenseEditRequest,
    ExpenseRequest,
    ExpenseResponse,
    create_expense_sync,
    delete_expense_sync,
    edit_expense_sync,
    list_expenses_sync,
)
from dinary.background.sheet_logging.sheet_logging import notify_new_work
from dinary.config import settings
from dinary.db.storage import get_db

router = APIRouter()


@router.post("/api/expenses", response_model=ExpenseResponse)
def create_expense(
    req: ExpenseRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ExpenseResponse:
    resp = create_expense_sync(req, con)
    if resp.status == "ok" and settings.sheet_logging_enabled:
        notify_new_work()
    return resp


@router.get("/api/expenses")
def get_expenses(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return list_expenses_sync(con, page, page_size)


@router.patch("/api/expenses/{expense_id}", status_code=204)
def patch_expense(
    expense_id: int,
    req: ExpenseEditRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    edit_expense_sync(expense_id, req, con)
    return Response(status_code=204)


@router.delete("/api/expenses/{expense_id}", status_code=204)
def delete_expense(
    expense_id: int,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    delete_expense_sync(expense_id, con)
    return Response(status_code=204)
