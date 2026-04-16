"""POST /api/expenses endpoint."""

import logging
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.services import duckdb_repo
from dinary.services.sync import schedule_sync

logger = logging.getLogger(__name__)
router = APIRouter()

TRAVEL_GROUP = "путешествия"


class ExpenseRequest(BaseModel):
    expense_id: str = Field(min_length=1)
    amount: float = Field(gt=0)
    currency: str = Field(default="RSD", pattern="^(RSD|EUR)$")
    category: str
    group: str = ""
    comment: str = ""
    date: date


class ExpenseResponse(BaseModel):
    status: Literal["created", "duplicate"]
    expense_id: str
    month: str
    category: str
    amount_rsd: float


@router.post("/api/expenses")
async def create_expense(req: ExpenseRequest):
    year = req.date.year

    con = duckdb_repo.get_budget_connection(year)
    try:
        mapping = duckdb_repo.resolve_mapping(con, req.category, req.group)
        if mapping is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown category mapping: {req.category} / {req.group}",
            )

        category_id = mapping["category_id"]
        beneficiary_id = mapping["beneficiary_id"]
        event_id = mapping["event_id"]
        store_id = mapping["store_id"]
        tag_ids = mapping["tag_ids"]

        if req.group == TRAVEL_GROUP:
            # resolve_travel_event writes to config.duckdb which is ATTACHed
            # read-only on `con` — close first to avoid file handle conflict
            con.close()
            event_id = duckdb_repo.resolve_travel_event(req.date)
            con = duckdb_repo.get_budget_connection(year)

        expense_dt = datetime.combine(req.date, datetime.min.time())

        try:
            result = duckdb_repo.insert_expense(
                con=con,
                expense_id=req.expense_id,
                expense_datetime=expense_dt,
                amount=req.amount,
                currency=req.currency,
                category_id=category_id,
                beneficiary_id=beneficiary_id,
                event_id=event_id,
                store_id=store_id,
                tag_ids=tag_ids,
                comment=req.comment,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
    finally:
        con.close()

    if result == "conflict":
        raise HTTPException(
            status_code=409,
            detail=f"Expense {req.expense_id} already exists with different data",
        )

    if result == "created":
        schedule_sync(
            year, req.date.month,
            sheet_category=req.category,
            sheet_group=req.group,
            amount=req.amount,
            comment=req.comment,
            expense_date=req.date,
        )

    month_label = req.date.strftime("%Y-%m")
    return ExpenseResponse(
        status=result,
        expense_id=req.expense_id,
        month=month_label,
        category=req.category,
        amount_rsd=req.amount,
    )
