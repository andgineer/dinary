"""POST /api/receipts — idempotent receipt ingestion.

Saves the raw URL and queues a classification job. Parsing and LLM
classification happen in the background drain, not on this hot path.
"""

import asyncio
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.background.receipt_classification_task import notify_new_receipt
from dinary.services import storage
from dinary.services.receipts import (
    get_receipt_by_client_id,
    insert_job,
    insert_receipt,
)

router = APIRouter()


class ReceiptRequest(BaseModel):
    client_receipt_id: str = Field(min_length=1)
    url: str = Field(min_length=1)


class ReceiptResponse(BaseModel):
    status: Literal["ok", "duplicate"]
    receipt_id: int


@router.post("/api/receipts", response_model=ReceiptResponse)
async def create_receipt(req: ReceiptRequest) -> ReceiptResponse:
    result = await asyncio.to_thread(_create_receipt_sync, req)
    if result.status == "ok":
        notify_new_receipt()
    return result


def _create_receipt_sync(req: ReceiptRequest) -> ReceiptResponse:
    conn = storage.get_connection()
    try:
        existing = get_receipt_by_client_id(conn, req.client_receipt_id)
        if existing:
            stored_receipt_id, stored_url = existing
            if stored_url != req.url:
                raise HTTPException(
                    status_code=409,
                    detail="client_receipt_id already exists with a different URL",
                )
            return ReceiptResponse(status="duplicate", receipt_id=stored_receipt_id)

        conn.execute("BEGIN IMMEDIATE")
        try:
            receipt_id = insert_receipt(conn, req.client_receipt_id, req.url)
            insert_job(conn, receipt_id)
            conn.execute("COMMIT")
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            existing = get_receipt_by_client_id(conn, req.client_receipt_id)
            if existing:
                return ReceiptResponse(status="duplicate", receipt_id=existing[0])
            raise
        except BaseException:
            conn.execute("ROLLBACK")
            raise

        return ReceiptResponse(status="ok", receipt_id=receipt_id)
    finally:
        conn.close()
