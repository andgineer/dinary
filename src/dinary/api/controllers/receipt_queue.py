"""Manual escape hatch for receipts stuck in the classification queue."""

import sqlite3
import uuid
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, Field

from dinary.adapters.exchange_rates import convert_to_accounting_amount
from dinary.adapters.serbian_receipt_parser import QrPayload, decode_qr_payload
from dinary.api.http_errors import value_error_as_422
from dinary.background.classification.persist import RECEIPT_CURRENCY
from dinary.background.sheet_logging.sheet_logging import notify_new_work
from dinary.config import settings
from dinary.db.expenses import enqueue_for_logging, validate_expense_refs
from dinary.db.receipts import complete_job
from dinary.db.storage import transaction
from dinary.sheets.sheet_mapping import resolve_effective_tag_ids


class ResolveReceiptRequest(BaseModel):
    category_id: int
    tag_ids: list[int] = Field(default_factory=list)
    event_id: int | None = None
    comment: str = ""


def list_stuck_receipts(con: sqlite3.Connection, page: int, page_size: int) -> dict:
    """Receipts whose job is poisoned or has been queued for over 5 minutes, oldest first."""
    offset = (page - 1) * page_size
    total = con.execute(
        """
        SELECT COUNT(*)
          FROM receipt_classification_jobs j
          JOIN receipts r ON r.id = j.receipt_id
         WHERE j.status = 'poisoned'
            OR r.created_at <= datetime('now', '-5 minutes')
        """,
    ).fetchone()[0]
    rows = con.execute(
        """
        SELECT r.id, r.url, r.store_name_raw, r.created_at,
               j.status, j.retry_count, j.last_error
          FROM receipt_classification_jobs j
          JOIN receipts r ON r.id = j.receipt_id
         WHERE j.status = 'poisoned'
            OR r.created_at <= datetime('now', '-5 minutes')
         ORDER BY r.created_at
         LIMIT ? OFFSET ?
        """,
        [page_size, offset],
    ).fetchall()

    items = []
    for row in rows:
        payload = decode_qr_payload(str(row["url"]))
        items.append(
            {
                "receipt_id": int(row["id"]),
                "status": str(row["status"]),
                "retry_count": int(row["retry_count"]),
                "last_error": str(row["last_error"]) if row["last_error"] else None,
                "created_at": str(row["created_at"]),
                "store_name_raw": str(row["store_name_raw"]) if row["store_name_raw"] else "",
                "amount": float(payload.amount) if payload else None,
                "currency": RECEIPT_CURRENCY if payload else None,
                "purchase_date": payload.purchase_datetime.isoformat() if payload else None,
            },
        )

    return {
        "items": items,
        "has_more": offset + page_size < total,
    }


def _load_active_job_receipt(con: sqlite3.Connection, receipt_id: int) -> sqlite3.Row:
    row = con.execute(
        """
        SELECT r.url, r.store_id, r.purchase_datetime, j.status
          FROM receipts r
          LEFT JOIN receipt_classification_jobs j ON j.receipt_id = r.id
         WHERE r.id = ?
        """,
        [receipt_id],
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if row["status"] is None:
        raise HTTPException(status_code=409, detail="Receipt already resolved")
    return row


def _resolve_expense_datetime(row: sqlite3.Row, payload: QrPayload) -> datetime:
    if row["purchase_datetime"]:
        purchase_dt_utc = datetime.fromisoformat(str(row["purchase_datetime"]))
    else:
        purchase_dt_utc = payload.purchase_datetime
    return purchase_dt_utc.astimezone(ZoneInfo(settings.user_timezone))


def _insert_resolved_expense(
    con: sqlite3.Connection,
    receipt_id: int,
    row: sqlite3.Row,
    req: ResolveReceiptRequest,
    expense_dt: datetime,
    amount_acc: Decimal,
    payload: QrPayload,
    tag_ids: list[int],
) -> int:
    if (
        con.execute(
            "SELECT 1 FROM receipt_classification_jobs WHERE receipt_id = ?",
            [receipt_id],
        ).fetchone()
        is None
    ):
        raise HTTPException(status_code=409, detail="Receipt already resolved")

    con.execute(
        """
        INSERT INTO expenses
               (client_expense_id, datetime, amount, amount_original, currency_original,
                category_id, confidence_level, comment, receipt_id, store_id, event_id,
                rule_id)
        VALUES (?, ?, ?, ?, ?, ?, 4, ?, ?, ?, ?, NULL)
        """,
        [
            str(uuid.uuid4()),
            expense_dt,
            float(amount_acc),
            float(payload.amount),
            RECEIPT_CURRENCY,
            req.category_id,
            req.comment,
            receipt_id,
            row["store_id"],
            req.event_id,
        ],
    )
    expense_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    enqueue_for_logging(con, expense_id)
    for tag_id in tag_ids:
        con.execute(
            "INSERT OR IGNORE INTO expense_tags (expense_id, tag_id) VALUES (?, ?)",
            [expense_id, tag_id],
        )
    complete_job(con, receipt_id)
    return expense_id


def resolve_receipt_manually(
    receipt_id: int,
    req: ResolveReceiptRequest,
    con: sqlite3.Connection,
) -> dict:
    row = _load_active_job_receipt(con, receipt_id)

    payload = decode_qr_payload(str(row["url"]))
    if payload is None:
        raise HTTPException(
            status_code=422,
            detail="Cannot determine purchase amount from this receipt's URL",
        )

    with value_error_as_422():
        validate_expense_refs(con, req.category_id, req.event_id, req.tag_ids)

    expense_dt = _resolve_expense_datetime(row, payload)
    with value_error_as_422():
        amount_acc = convert_to_accounting_amount(
            con,
            payload.amount,
            RECEIPT_CURRENCY,
            expense_dt.date(),
        )
    tag_ids = resolve_effective_tag_ids(con, req.tag_ids, req.event_id)

    with transaction(con):
        expense_id = _insert_resolved_expense(
            con,
            receipt_id,
            row,
            req,
            expense_dt,
            amount_acc,
            payload,
            tag_ids,
        )

    if settings.sheet_logging_enabled:
        notify_new_work()

    return {
        "status": "ok",
        "expense_id": expense_id,
        "amount_original": float(payload.amount),
        "currency_original": RECEIPT_CURRENCY,
        "category_id": req.category_id,
    }
