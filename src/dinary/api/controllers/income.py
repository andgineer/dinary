"""Income business logic: currency conversion, Pydantic models, controller functions."""

import sqlite3
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.adapters.exchange_rates import convert_to_accounting_amount
from dinary.api.http_errors import value_error_as_422
from dinary.config import settings
from dinary.db.income import (
    IncomeData,
    IncomeRow,
    delete_income,
    get_income_by_id,
    insert_income,
    list_incomes,
    update_income,
)
from dinary.db.storage import transaction


class IncomeCreateRequest(BaseModel):
    year: int
    month: int
    income_date: date
    amount_original: Decimal = Field(gt=Decimal(0))
    currency_original: str
    comment: str | None = None


class IncomeUpdateRequest(BaseModel):
    year: int | None = None
    month: int | None = None
    amount_original: Decimal | None = Field(default=None, gt=Decimal(0))
    currency_original: str | None = None
    income_date: date | None = None
    comment: str | None = None


class IncomeResponse(BaseModel):
    id: int
    year: int
    month: int
    income_date: date
    amount: float
    amount_original: float
    currency_original: str
    comment: str | None
    currency: str


class IncomeListResponse(BaseModel):
    items: list[IncomeResponse]
    has_more: bool


def _row_to_response(row: IncomeRow) -> IncomeResponse:
    return IncomeResponse(
        id=row.id,
        year=row.year,
        month=row.month,
        income_date=row.income_date,
        amount=float(row.amount),
        amount_original=float(row.amount_original),
        currency_original=row.currency_original,
        comment=row.comment,
        currency=settings.accounting_currency,
    )


def create_income_sync(req: IncomeCreateRequest, con: sqlite3.Connection) -> IncomeResponse:
    with value_error_as_422():
        amount = float(
            convert_to_accounting_amount(
                con,
                req.amount_original,
                req.currency_original.upper(),
                req.income_date,
            ),
        )
    data = IncomeData(
        year=req.year,
        month=req.month,
        income_date=req.income_date,
        amount=amount,
        amount_original=float(req.amount_original),
        currency_original=req.currency_original.upper(),
        comment=req.comment,
    )
    with transaction(con):
        row = insert_income(con, data, enqueue_logging=settings.sheet_logging_enabled)
    return _row_to_response(row)


def update_income_sync(
    income_id: int,
    req: IncomeUpdateRequest,
    con: sqlite3.Connection,
) -> IncomeResponse:
    existing = get_income_by_id(con, income_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Income {income_id} not found")
    new_year = req.year if req.year is not None else existing.year
    new_month = req.month if req.month is not None else existing.month
    new_income_date = req.income_date or existing.income_date
    new_currency = (req.currency_original or existing.currency_original).upper()
    new_amount_original = (
        req.amount_original if req.amount_original is not None else existing.amount_original
    )
    new_comment = req.comment if req.comment is not None else existing.comment
    with value_error_as_422():
        amount = float(
            convert_to_accounting_amount(con, new_amount_original, new_currency, new_income_date),
        )
    data = IncomeData(
        year=new_year,
        month=new_month,
        income_date=new_income_date,
        amount=amount,
        amount_original=float(new_amount_original),
        currency_original=new_currency,
        comment=new_comment,
    )
    try:
        with transaction(con):
            row = update_income(
                con,
                income_id,
                data,
                enqueue_logging=settings.sheet_logging_enabled,
            )
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Income {income_id} not found") from None
    return _row_to_response(row)


def delete_income_sync(income_id: int, con: sqlite3.Connection) -> None:
    try:
        with transaction(con):
            delete_income(con, income_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Income {income_id} not found") from None


def list_incomes_sync(con: sqlite3.Connection, page: int, page_size: int) -> IncomeListResponse:
    rows, has_more = list_incomes(con, page, page_size)
    return IncomeListResponse(
        items=[_row_to_response(r) for r in rows],
        has_more=has_more,
    )
