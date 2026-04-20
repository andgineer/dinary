"""POST /api/expenses endpoint (idempotent on client_expense_id)."""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

import duckdb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.config import settings
from dinary.services import duckdb_repo
from dinary.services.nbs import convert
from dinary.services.sheet_logging import is_sheet_logging_enabled
from dinary.services.sql_loader import load_sql

router = APIRouter()


class ExpenseRequest(BaseModel):
    client_expense_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=Decimal(0))
    currency: str | None = None
    category: str
    comment: str | None = None
    date: date


class ExpenseResponse(BaseModel):
    status: Literal["ok", "duplicate"]
    month: str
    category: str
    amount_original: Decimal
    currency_original: str
    catalog_version: int


@router.post("/api/expenses", response_model=ExpenseResponse)
async def create_expense(req: ExpenseRequest) -> ExpenseResponse:
    # DuckDB / NBS calls are synchronous and would otherwise block every
    # other request on the single-worker event loop. Offload the whole
    # body to the default thread pool; the lifespan drain already does
    # the same for its own blocking work.
    return await asyncio.to_thread(_create_expense_sync, req)


def _create_expense_sync(req: ExpenseRequest) -> ExpenseResponse:
    currency = req.currency or settings.app_currency

    con = duckdb_repo.get_connection()
    try:
        category_id = _resolve_category_for_write(con, req)

        amount_app = req.amount
        if currency.upper() != settings.app_currency.upper():
            amount_app, _rate = convert(
                con,
                req.amount,
                currency,
                settings.app_currency,
                req.date,
            )

        expense_dt = datetime.combine(req.date, datetime.min.time())

        # ``insert_expense`` is the single source of truth for the
        # duplicate-vs-conflict decision: it runs the INSERT with
        # ``ON CONFLICT DO NOTHING`` and, on conflict, compares the full
        # stored payload against the incoming one. We intentionally do
        # not pre-check with ``lookup_existing_expense`` for the
        # happy-path compare — a double compare would drift over time,
        # and the ON CONFLICT path is already atomic against concurrent
        # POSTs sharing the same ``client_expense_id``.
        try:
            result = duckdb_repo.insert_expense(
                con,
                client_expense_id=req.client_expense_id,
                expense_datetime=expense_dt,
                amount=float(amount_app),
                amount_original=float(req.amount),
                currency_original=currency,
                category_id=category_id,
                event_id=None,
                comment=req.comment or "",
                sheet_category=None,
                sheet_group=None,
                tag_ids=[],
                enqueue_logging=is_sheet_logging_enabled(),
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        # Any other exception (duckdb.ConstraintException from an unexpected
        # FK/UNIQUE violation, RuntimeError from disk I/O, etc.) propagates
        # as a plain 500. Phase-1 callers should never see those.

        if result == "conflict":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"client_expense_id {req.client_expense_id!r} already exists "
                    "with different data"
                ),
            )

        catalog_version = duckdb_repo.get_catalog_version(con)
    finally:
        con.close()

    return ExpenseResponse(
        status="ok" if result == "created" else "duplicate",
        month=req.date.strftime("%Y-%m"),
        category=req.category,
        amount_original=req.amount,
        currency_original=currency,
        catalog_version=catalog_version,
    )


def _resolve_category_for_write(
    con: duckdb.DuckDBPyConnection,
    req: ExpenseRequest,
) -> int:
    """Resolve ``req.category`` to a category id usable for this POST.

    Active categories pass through immediately. A *known-but-inactive*
    category is only accepted when the POST is provably an idempotent
    replay: there is already an ``expenses`` row with this
    ``client_expense_id`` whose stored ``category_id`` matches the
    name we just resolved. This closes the PWA offline-replay hole —
    an operator reseed that deactivates a category after the PWA has
    queued an expense against it must not silently drop the retry on
    the floor. A truly-new POST against a retired category is still
    rejected as 422.
    """
    row = con.execute(
        load_sql("get_category_by_name.sql"),
        [req.category],
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown category: {req.category!r}",
        )
    category_id, is_active = int(row[0]), bool(row[1])
    if is_active:
        return category_id

    existing = duckdb_repo.lookup_existing_expense(
        req.client_expense_id,
        con=con,
    )
    if existing is not None and existing.category_id == category_id:
        return category_id

    raise HTTPException(
        status_code=422,
        detail=f"Inactive category: {req.category!r}",
    )
