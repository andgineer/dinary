"""POST /api/receipts — idempotent receipt ingestion.

Saves the raw URL and queues a classification job. Parsing and LLM
classification happen in the background drain, not on this hot path.
"""

import asyncio
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dinary.background.classification.task import notify_new_receipt
from dinary.db.receipts import (
    get_receipt_by_client_id,
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
async def create_receipt(
    req: ReceiptRequest,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> ReceiptResponse:
    result = await asyncio.to_thread(_create_receipt_sync, req, con)
    if result.status == "ok":
        notify_new_receipt()
    return result


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
