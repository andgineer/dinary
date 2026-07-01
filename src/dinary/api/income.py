"""Income API: GET /api/incomes, POST /api/incomes,
PATCH /api/incomes/{id}, DELETE /api/incomes/{id}"""

import sqlite3

from fastapi import APIRouter, Depends, Query, Response

from dinary.api.controllers.income import (
    IncomeCreateRequest,
    IncomeListResponse,
    IncomeUpdateRequest,
    create_income_sync,
    delete_income_sync,
    list_incomes_sync,
    update_income_sync,
)
from dinary.background.sheet_logging.sheet_logging import notify_new_work
from dinary.config import settings
from dinary.db.storage import get_db

router = APIRouter()


@router.get("/api/incomes", response_model=IncomeListResponse)
def get_incomes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> IncomeListResponse:
    return list_incomes_sync(con, page, page_size)


@router.post("/api/incomes", status_code=204)
def create_income(
    req: IncomeCreateRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    create_income_sync(req, con)
    if settings.sheet_logging_enabled:
        notify_new_work()
    return Response(status_code=204)


@router.patch("/api/incomes/{income_id}", status_code=204)
def patch_income(
    income_id: int,
    req: IncomeUpdateRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    update_income_sync(income_id, req, con)
    if settings.sheet_logging_enabled:
        notify_new_work()
    return Response(status_code=204)


@router.delete("/api/incomes/{income_id}", status_code=204)
def delete_income(
    income_id: int,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    delete_income_sync(income_id, con)
    return Response(status_code=204)
