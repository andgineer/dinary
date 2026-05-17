"""Currency picker HTTP surface.

The PWA owns its picker state: which currency codes the operator
keeps as quick-pick chips, which one they last selected, and so on.
**It does not need rates** — the server is the source of truth for
exchange-rate conversion, which happens at write time inside
``POST /api/expenses`` (the audit tuple ``(amount_original,
currency_original)`` is stored verbatim and the NBS-anchored
conversion to ``settings.accounting_currency`` is computed and
written there). Therefore the PWA-facing surface is intentionally
limited to the saved-codes CRUD; no rate endpoints exist.

Endpoints
---------

* ``GET    /api/currencies``                    -> saved currency codes
* ``POST   /api/currencies``       {code: ABC}  -> add / idempotent
* ``DELETE /api/currencies/{code}``             -> remove

Authentication is currently inherited from the rest of the admin
surface: deferred to the future auth pass; deployments are expected
to put the service behind a private network / ACL.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from dinary.config import settings
from dinary.services import currencies, storage

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
    try:
        return currencies._normalise_code(code)  # noqa: SLF001
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _list_response(con) -> CurrencyListResponse:
    return CurrencyListResponse(
        codes=currencies.list_currencies(con),
        default_code=settings.app_currency.upper(),
    )


@router.get("/api/currencies", response_model=CurrencyListResponse)
def get_currencies() -> CurrencyListResponse:
    con = storage.get_connection()
    try:
        return _list_response(con)
    finally:
        con.close()


@router.post("/api/currencies", response_model=CurrencyListResponse)
def add_currency(body: CurrencyAddBody) -> CurrencyListResponse:
    code = _normalise_code_or_400(body.code)
    con = storage.get_connection()
    try:
        currencies.add_currency(con, code)
        return _list_response(con)
    finally:
        con.close()


@router.delete("/api/currencies/{code}", response_model=CurrencyListResponse)
def delete_currency(code: str) -> CurrencyListResponse:
    canonical = _normalise_code_or_400(code)
    if canonical == settings.app_currency.upper():
        # The default currency is the operator's input currency for
        # POST /api/expenses; removing it would leave the picker
        # without a fallback and break the expense form.
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete the default currency {canonical!r}",
        )
    con = storage.get_connection()
    try:
        currencies.remove_currency(con, canonical)
        return _list_response(con)
    finally:
        con.close()
