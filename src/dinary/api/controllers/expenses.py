"""Expense creation business logic."""

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.adapters.exchange_rates import get_rate
from dinary.api.controllers.catalog import most_used_category_per_group, most_used_group
from dinary.config import settings
from dinary.db.catalog import get_catalog_version
from dinary.db.expenses import (
    ExpensePayload,
    describe_expense_conflict,
    insert_expense,
    lookup_existing_expense,
)
from dinary.sheets import sheet_mapping


class ExpenseRequest(BaseModel):
    client_expense_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=Decimal(0))
    currency: str | None = None
    category_id: int
    event_id: int | None = None
    tag_ids: list[int] = Field(default_factory=list)
    comment: str | None = None
    date: date


class ExpenseResponse(BaseModel):
    status: Literal["ok", "duplicate"]
    month: str
    category_id: int
    amount_original: Decimal
    currency_original: str
    catalog_version: int
    default_group_id: int | None = None
    default_category_ids: dict[str, int] = Field(default_factory=dict)


def create_expense_sync(req: ExpenseRequest, con: sqlite3.Connection) -> ExpenseResponse:
    currency = req.currency or settings.app_currency

    _resolve_category_for_write(con, req)
    _validate_event(con, req)
    _validate_tags(con, req)

    amount_acc = req.amount
    if currency.upper() != settings.accounting_currency.upper():
        rate = get_rate(con, req.date, currency, settings.accounting_currency, offline=True)
        amount_acc = (req.amount * rate).quantize(Decimal("0.01"))

    expense_dt = datetime.combine(req.date, datetime.min.time())

    effective_tag_ids: list[int] = list(dict.fromkeys(int(t) for t in req.tag_ids))
    if req.event_id is not None:
        for auto_id in sheet_mapping.resolve_event_auto_tag_ids(con, req.event_id):
            if auto_id not in effective_tag_ids:
                effective_tag_ids.append(auto_id)

    amount_acc_f = float(amount_acc)
    amount_orig_f = float(req.amount)
    comment = req.comment or ""
    try:
        result = insert_expense(
            con,
            ExpensePayload(
                client_expense_id=req.client_expense_id,
                expense_datetime=expense_dt,
                amount=amount_acc_f,
                amount_original=amount_orig_f,
                currency_original=currency,
                category_id=req.category_id,
                event_id=req.event_id,
                comment=comment,
                sheet_category=None,
                sheet_group=None,
                tag_ids=effective_tag_ids,
            ),
            enqueue_logging=settings.sheet_logging_enabled,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None

    if result == "conflict":
        diff = describe_expense_conflict(
            con,
            ExpensePayload(
                client_expense_id=req.client_expense_id,
                expense_datetime=expense_dt,
                amount=amount_acc_f,
                amount_original=amount_orig_f,
                currency_original=currency,
                category_id=req.category_id,
                event_id=req.event_id,
                comment=comment,
                sheet_category=None,
                sheet_group=None,
                tag_ids=effective_tag_ids,
            ),
        )
        detail = f"client_expense_id {req.client_expense_id!r} already exists with different data"
        if diff:
            detail = f"{detail}: {diff}"
        raise HTTPException(status_code=409, detail=detail)

    catalog_version = get_catalog_version(con)
    default_group_id = most_used_group(con)
    category_defaults = most_used_category_per_group(con)

    return ExpenseResponse(
        status="ok" if result == "created" else "duplicate",
        month=req.date.strftime("%Y-%m"),
        category_id=req.category_id,
        amount_original=req.amount,
        currency_original=currency,
        catalog_version=catalog_version,
        default_group_id=default_group_id,
        default_category_ids={str(k): v for k, v in category_defaults.items()},
    )


def _is_replay(con: sqlite3.Connection, client_expense_id: str) -> bool:
    return lookup_existing_expense(client_expense_id, con=con) is not None


def _resolve_category_for_write(con: sqlite3.Connection, req: ExpenseRequest) -> None:
    row = con.execute(
        "SELECT id, is_active FROM categories WHERE id = ?",
        [req.category_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail=f"Unknown category_id: {req.category_id}")
    if bool(row[1]):
        return
    if _is_replay(con, req.client_expense_id):
        return
    raise HTTPException(status_code=422, detail=f"Inactive category_id: {req.category_id}")


def _validate_event(con: sqlite3.Connection, req: ExpenseRequest) -> None:
    if req.event_id is None:
        return
    row = con.execute("SELECT is_active FROM events WHERE id = ?", [req.event_id]).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail=f"Unknown event_id: {req.event_id}")
    if bool(row[0]):
        return
    if _is_replay(con, req.client_expense_id):
        return
    raise HTTPException(status_code=422, detail=f"Inactive event_id: {req.event_id}")


def _validate_tags(con: sqlite3.Connection, req: ExpenseRequest) -> None:
    if not req.tag_ids:
        return
    unique_ids = list({int(t) for t in req.tag_ids})
    placeholders = ",".join(["?"] * len(unique_ids))
    rows = con.execute(
        f"SELECT id, is_active FROM tags WHERE id IN ({placeholders})",  # noqa: S608
        unique_ids,
    ).fetchall()
    found = {int(r[0]): bool(r[1]) for r in rows}
    missing = [t for t in unique_ids if t not in found]
    if missing:
        raise HTTPException(status_code=422, detail=f"Unknown tag_ids: {missing}")
    inactive = [t for t in unique_ids if not found[t]]
    if not inactive:
        return
    if _is_replay(con, req.client_expense_id):
        return
    raise HTTPException(status_code=422, detail=f"Inactive tag_ids: {inactive}")
