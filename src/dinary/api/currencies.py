"""Currencies API: /api/currencies"""

import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from dinary.api.http_errors import value_error_as_422
from dinary.config import settings
from dinary.db import currencies
from dinary.db.storage import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class CurrencyAddBody(BaseModel):
    code: str = Field(min_length=3, max_length=3)


class CurrencyListResponse(BaseModel):
    """Saved-currency list returned by GET/POST/DELETE /api/currencies.

    ``default_code`` is the env-seeded ``settings.app_currency`` and
    is reported separately so the PWA can pin it in the picker and
    refuse to delete it client-side. Server still enforces the rule.
    """

    codes: list[str]
    default_code: str


def _normalise_code_or_400(code: str) -> str:
    with value_error_as_422():
        return currencies._normalise_code(code)  # noqa: SLF001


def _list_response(con) -> CurrencyListResponse:
    return CurrencyListResponse(
        codes=currencies.list_currencies(con),
        default_code=settings.app_currency.upper(),
    )


@router.get("/api/currencies", response_model=CurrencyListResponse)
def get_currencies(con: sqlite3.Connection = Depends(get_db)) -> CurrencyListResponse:  # noqa: B008
    return _list_response(con)


@router.post("/api/currencies", response_model=CurrencyListResponse)
def add_currency(
    body: CurrencyAddBody,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CurrencyListResponse:
    code = _normalise_code_or_400(body.code)
    currencies.add_currency(con, code)
    return _list_response(con)


@router.delete("/api/currencies/{code}", response_model=CurrencyListResponse)
def delete_currency(
    code: str,
    con: sqlite3.Connection = Depends(get_db),  # noqa: B008
) -> CurrencyListResponse:
    canonical = _normalise_code_or_400(code)
    if canonical == settings.app_currency.upper():
        # The default currency is the operator's input currency for
        # POST /api/expenses; removing it would leave the picker
        # without a fallback and break the expense form.
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete the default currency {canonical!r}",
        )
    currencies.remove_currency(con, canonical)
    return _list_response(con)
