"""POST /api/qr/parse endpoint."""

import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, HttpUrl

from dinary.services.qr_parser import parse_receipt_url

logger = logging.getLogger(__name__)
router = APIRouter()


class QrParseRequest(BaseModel):
    url: HttpUrl


class QrParseResponse(BaseModel):
    amount: float
    date: date


@router.post("/api/qr/parse", response_model=QrParseResponse)
def parse_qr(req: QrParseRequest) -> QrParseResponse:
    try:
        result = parse_receipt_url(str(req.url))
    except Exception:
        logger.exception("QR parse failed for %s", req.url)
        raise HTTPException(status_code=502, detail="Could not parse receipt from URL")

    return QrParseResponse(amount=result.amount, date=result.date)
