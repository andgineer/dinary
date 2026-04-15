"""POST /api/expenses endpoint."""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.services import sheets

logger = logging.getLogger(__name__)
router = APIRouter()


class ExpenseRequest(BaseModel):
    amount: float = Field(gt=0)
    currency: str = Field(default="RSD", pattern="^(RSD|EUR)$")
    category: str
    group: str = ""
    comment: str = ""
    date: date


class ExpenseResponse(BaseModel):
    month: str
    category: str
    amount_rsd: float
    amount_eur: float
    new_total_rsd: float


@router.post("/api/expenses", response_model=ExpenseResponse)
async def create_expense(req: ExpenseRequest) -> ExpenseResponse:
    if not sheets.validate_category(req.category, req.group):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown category: {req.category} / {req.group}",
        )

    try:
        result = await sheets.write_expense(
            amount_rsd=req.amount,
            category=req.category,
            group=req.group,
            comment=req.comment,
            expense_date=req.date,
        )
    except Exception:
        logger.exception("Google Sheets write failed")
        raise HTTPException(
            status_code=502,
            detail="Google Sheets write failed, entry queued for retry",
        ) from None

    return ExpenseResponse(**result)
