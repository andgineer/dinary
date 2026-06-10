"""Receipts API: POST, GET, DELETE /api/receipts."""

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from dinary.api.controllers.receipt_queue import (
    ResolveReceiptRequest,
    list_stuck_receipts,
    resolve_receipt_manually,
)
from dinary.background.classification.task import notify_new_receipt
from dinary.db.receipts import (
    delete_receipt_cascade,
    get_receipt_by_client_id,
    get_receipt_summary,
    insert_job,
    insert_receipt,
)
from dinary.db.storage import get_db, transaction

router = APIRouter()


class ReceiptRequest(BaseModel):
    client_receipt_id: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ReceiptResponse(BaseModel):
    status: Literal["ok", "duplicate"]
    receipt_id: int


@router.post("/api/receipts", response_model=ReceiptResponse)
def create_receipt(
    req: ReceiptRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ReceiptResponse:
    result = _create_receipt_sync(req, con)
    if result.status == "ok":
        notify_new_receipt()
    return result


@router.get("/api/receipts/queue")
def receipt_queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return list_stuck_receipts(con, page, page_size)


@router.post("/api/receipts/{receipt_id}/resolve")
def resolve_receipt(
    receipt_id: int,
    body: ResolveReceiptRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    return resolve_receipt_manually(receipt_id, body, con)


@router.get("/api/receipts/{receipt_id}")
def get_receipt(
    receipt_id: int,
    include: str = Query(""),
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> dict:
    summary = get_receipt_summary(con, receipt_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    if "expenses" not in include.split(","):
        summary.pop("expenses", None)
    return summary


@router.delete("/api/receipts/{receipt_id}", status_code=204)
def delete_receipt(
    receipt_id: int,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> Response:
    summary = get_receipt_summary(con, receipt_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Receipt not found")
    delete_receipt_cascade(con, receipt_id)
    return Response(status_code=204)


def _create_receipt_sync(req: ReceiptRequest, con: sqlite3.Connection) -> ReceiptResponse:
    existing = get_receipt_by_client_id(con, req.client_receipt_id)
    if existing:
        stored_receipt_id, stored_url = existing
        if stored_url != req.url:
            raise HTTPException(
                status_code=409,
                detail="client_receipt_id already exists with a different URL",
            )
        return ReceiptResponse(status="duplicate", receipt_id=stored_receipt_id)

    try:
        with transaction(con):
            receipt_id = insert_receipt(con, req.client_receipt_id, req.url)
            insert_job(con, receipt_id)
    except sqlite3.IntegrityError:
        # Concurrent insert won the race; re-query to return the winning row.
        existing = get_receipt_by_client_id(con, req.client_receipt_id)
        if existing:
            return ReceiptResponse(status="duplicate", receipt_id=existing[0])
        raise

    return ReceiptResponse(status="ok", receipt_id=receipt_id)
