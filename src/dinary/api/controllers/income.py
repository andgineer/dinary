"""Income business logic: currency conversion, Pydantic models, controller functions."""

import sqlite3
from datetime import date
from decimal import Decimal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.adapters.exchange_rates import get_rate
from dinary.config import settings
from dinary.db.income import (
    IncomeRow,
    delete_income,
    get_income_by_year_month,
    insert_income,
    list_incomes,
    update_income,
)
from dinary.db.storage import transaction


class IncomeCreateRequest(BaseModel):
    year: int = Field(ge=1, le=9999)
    month: int = Field(ge=1, le=12)
    amount_original: Decimal = Field(gt=Decimal(0))
    currency_original: str


class IncomeUpdateRequest(BaseModel):
    amount_original: Decimal | None = Field(default=None, gt=Decimal(0))
    currency_original: str | None = None


class IncomeResponse(BaseModel):
    year: int
    month: int
    amount: float
    currency: str


class IncomeListResponse(BaseModel):
    items: list[IncomeResponse]
    has_more: bool


def _convert_to_accounting(
    con: sqlite3.Connection,
    amount_original: Decimal,
    currency_original: str,
    for_date: date,
) -> float:
    currency = currency_original.upper()
    if currency == settings.accounting_currency.upper():
        return float(amount_original)
    rate = get_rate(con, for_date, currency, settings.accounting_currency, offline=True)
    return float((amount_original * rate).quantize(Decimal("0.01")))


def _row_to_response(row: IncomeRow) -> IncomeResponse:
    return IncomeResponse(
        year=row.year,
        month=row.month,
        amount=float(row.amount),
        currency=settings.accounting_currency,
    )


def create_income_sync(req: IncomeCreateRequest, con: sqlite3.Connection) -> IncomeResponse:
    for_date = date(req.year, req.month, 1)
    amount = _convert_to_accounting(con, req.amount_original, req.currency_original, for_date)
    try:
        with transaction(con):
            insert_income(
                con,
                req.year,
                req.month,
                amount,
                enqueue_logging=settings.sheet_logging_enabled,
            )
    except sqlite3.IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Income for {req.year}-{req.month:02d} already exists",
        ) from None
    return IncomeResponse(
        year=req.year,
        month=req.month,
        amount=amount,
        currency=settings.accounting_currency,
    )


def update_income_sync(
    year: int,
    month: int,
    req: IncomeUpdateRequest,
    con: sqlite3.Connection,
) -> IncomeResponse:
    existing = get_income_by_year_month(con, year, month)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Income ({year}, {month}) not found")
    if req.amount_original is None:
        if req.currency_original is not None:
            raise HTTPException(
                status_code=422,
                detail="currency_original requires amount_original",
            )
        return _row_to_response(existing)
    new_currency = (req.currency_original or settings.accounting_currency).upper()
    for_date = date(year, month, 1)
    amount = _convert_to_accounting(con, req.amount_original, new_currency, for_date)
    try:
        with transaction(con):
            row = update_income(
                con,
                year,
                month,
                amount,
                enqueue_logging=settings.sheet_logging_enabled,
            )
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Income ({year}, {month}) not found") from None
    return _row_to_response(row)


def delete_income_sync(year: int, month: int, con: sqlite3.Connection) -> None:
    try:
        with transaction(con):
            delete_income(con, year, month)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Income ({year}, {month}) not found") from None


def list_incomes_sync(con: sqlite3.Connection, page: int, page_size: int) -> IncomeListResponse:
    rows, has_more = list_incomes(con, page, page_size)
    return IncomeListResponse(
        items=[
            IncomeResponse(
                year=r.year,
                month=r.month,
                amount=float(r.amount),
                currency=settings.accounting_currency,
            )
            for r in rows
        ],
        has_more=has_more,
    )
